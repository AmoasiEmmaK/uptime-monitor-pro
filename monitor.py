"""
Core monitor engine.

Checks a list of servers/services on a schedule and logs the results
(up/down, response time) to a local SQLite database. Supports two
check types:
  - "ping": ICMP ping, for raw IPs/hostnames (e.g. routers, internal boxes)
  - "http": HTTP(S) request, for web services/URLs (checks for a
    successful status code)

Run this as a background/scheduled process:
    python monitor.py

It will loop forever, checking all servers every CHECK_INTERVAL_SECONDS.
Stop it with Ctrl+C.
"""

import sqlite3
import os
import subprocess
import platform
import socket
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

import alerts

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uptime.db")
CHECK_INTERVAL_SECONDS = 60  # how often to re-check all servers

# Tracks each server's last known status in memory, so we can detect
# transitions (up->down, down->up) and only alert on the change, not
# on every single check. Resets when the script restarts, which just
# means the first check after a restart won't trigger an alert (it
# takes two checks to see a "change") — acceptable for a v1.
# Tracks each server's last CONFIRMED status (after applying its
# alert_threshold) and its current run of consecutive failed checks.
# Resets when the script restarts — see handle_monitoring_gap.
_confirmed_status = {}
_consecutive_failures = {}


def init_db():
    """Create the tables if they don't exist yet."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            check_type TEXT NOT NULL DEFAULT 'ping',
            active INTEGER NOT NULL DEFAULT 1,
            user_id INTEGER,
            alert_threshold INTEGER NOT NULL DEFAULT 1,
            maintenance_mode INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Migration: add check_type column if it doesn't exist yet
    # (for anyone running this against a database created before this feature)
    cur.execute("PRAGMA table_info(servers)")
    existing_columns = [row[1] for row in cur.fetchall()]
    if "check_type" not in existing_columns:
        cur.execute("ALTER TABLE servers ADD COLUMN check_type TEXT NOT NULL DEFAULT 'ping'")
    if "user_id" not in existing_columns:
        # Existing servers from before multi-tenancy have no owner (NULL).
        # They simply won't show up for any account until reassigned —
        # this is expected for a dev/test database like this one.
        cur.execute("ALTER TABLE servers ADD COLUMN user_id INTEGER")
    if "alert_threshold" not in existing_columns:
        # Number of consecutive failed checks required before a server
        # is considered "down" and triggers an incident/alert. Default
        # of 1 preserves the old behavior (alert immediately) for
        # existing servers; new servers can be configured higher to
        # filter out brief network blips.
        cur.execute("ALTER TABLE servers ADD COLUMN alert_threshold INTEGER NOT NULL DEFAULT 1")
    if "maintenance_mode" not in existing_columns:
        cur.execute("ALTER TABLE servers ADD COLUMN maintenance_mode INTEGER NOT NULL DEFAULT 0")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            is_up INTEGER NOT NULL,
            response_time_ms REAL,
            FOREIGN KEY (server_id) REFERENCES servers (id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            note TEXT,
            FOREIGN KEY (server_id) REFERENCES servers (id)
        )
    """)

    # Migration: add note column if it doesn't exist yet
    cur.execute("PRAGMA table_info(incidents)")
    incident_columns = [row[1] for row in cur.fetchall()]
    if "note" not in incident_columns:
        cur.execute("ALTER TABLE incidents ADD COLUMN note TEXT")

    conn.commit()
    conn.close()


def add_server(name: str, address: str, check_type: str = "ping", user_id: int = None, alert_threshold: int = 1):
    """
    Add a server/service to monitor, owned by a specific user account.

    check_type: "ping" for an IP/hostname, "http" for a URL,
    "tcp" for host:port, "dns" for a hostname to resolve.
    user_id: the owning account's id (from auth.get_user_id). Servers
    are only ever shown to the account that added them.
    alert_threshold: number of consecutive failed checks required
    before this server is considered "down" (opens an incident and
    sends an alert). 1 = alert immediately (old behavior). Higher
    values filter out brief blips at the cost of slower detection.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO servers (name, address, check_type, user_id, alert_threshold) VALUES (?, ?, ?, ?, ?)",
        (name, address, check_type, user_id, alert_threshold),
    )
    conn.commit()
    conn.close()


def get_all_servers_for_user(user_id: int):
    """Return every server (active and inactive) owned by this user."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, name, address, check_type, active, alert_threshold, maintenance_mode FROM servers WHERE user_id = ? ORDER BY name",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def update_server(server_id: int, name: str, address: str, check_type: str, user_id: int, alert_threshold: int = 1, maintenance_mode: bool = False):
    """Update a server's name/address/check type/alert threshold — only if owned by this user."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE servers SET name = ?, address = ?, check_type = ?, alert_threshold = ?, maintenance_mode = ? WHERE id = ? AND user_id = ?",
        (name, address, check_type, alert_threshold, 1 if maintenance_mode else 0, server_id, user_id),
    )
    conn.commit()
    conn.close()


def set_server_active(server_id: int, active: bool, user_id: int):
    """Activate/deactivate a server — only if owned by this user."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE servers SET active = ? WHERE id = ? AND user_id = ?",
        (1 if active else 0, server_id, user_id),
    )
    conn.commit()
    conn.close()


def delete_server_permanently(server_id: int, user_id: int):
    """
    Permanently delete a server and all its history (checks and
    incidents) — only if owned by this user. This cannot be undone —
    prefer set_server_active(False) if you just want to stop
    monitoring something but keep its history.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM servers WHERE id = ? AND user_id = ?", (server_id, user_id))
    if cur.fetchone() is None:
        conn.close()
        return  # not owned by this user, refuse silently
    cur.execute("DELETE FROM checks WHERE server_id = ?", (server_id,))
    cur.execute("DELETE FROM incidents WHERE server_id = ?", (server_id,))
    cur.execute("DELETE FROM servers WHERE id = ? AND user_id = ?", (server_id, user_id))
    conn.commit()
    conn.close()


def get_active_servers():
    """
    Return ALL active servers across every user account. Used only by
    the background monitor.py loop, which checks everyone's servers
    regardless of owner — ownership only matters for what the
    dashboard displays to each logged-in user.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, address, check_type, alert_threshold, maintenance_mode FROM servers WHERE active = 1")
    rows = cur.fetchall()
    conn.close()
    return rows


def check_server_now(server_id: int, user_id: int):
    """Run and log one immediate check, only for a server owned by the user."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT address, check_type FROM servers WHERE id = ? AND user_id = ?",
        (server_id, user_id),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    is_up, response_time_ms = check_server(row[0], row[1])
    log_check(server_id, is_up, response_time_ms)
    return is_up, response_time_ms


def ping_host(address: str):
    """
    Ping a host using the system ping command (works cross-platform,
    no extra dependencies needed).

    Returns (is_up: bool, response_time_ms: float | None)
    """
    is_windows = platform.system().lower() == "windows"
    count_flag = "-n" if is_windows else "-c"
    timeout_flag = "-w" if is_windows else "-W"
    # Windows timeout is in ms, Linux/Mac timeout is in seconds
    timeout_value = "2000" if is_windows else "2"

    cmd = ["ping", count_flag, "1", timeout_flag, timeout_value, address]

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        elapsed_ms = (time.time() - start) * 1000
        is_up = result.returncode == 0
        return is_up, round(elapsed_ms, 1) if is_up else None
    except subprocess.TimeoutExpired:
        return False, None


def http_check(url: str, timeout_seconds: int = 5):
    """
    Check an HTTP(S) URL by making a request and checking for a
    successful status code (< 400). Uses only the standard library,
    no extra dependencies needed.

    Returns (is_up: bool, response_time_ms: float | None)
    """
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "uptime-monitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            elapsed_ms = (time.time() - start) * 1000
            is_up = response.status < 400
            return is_up, round(elapsed_ms, 1) if is_up else None
    except urllib.error.HTTPError:
        # Server responded, but with an error status (4xx/5xx) — still "reachable"
        # but treated as down since the service itself isn't healthy.
        elapsed_ms = (time.time() - start) * 1000
        return False, None
    except Exception:
        # DNS failure, connection refused, timeout, etc.
        return False, None


def tcp_check(address: str, timeout_seconds: int = 3):
    """
    Check whether a TCP port is open by attempting a connection.
    Useful for checking specific services (databases, mail servers,
    custom apps) rather than just "is the host reachable at all".

    Expects address in "host:port" format, e.g. "192.168.1.10:3306".

    Returns (is_up: bool, response_time_ms: float | None)
    """
    if ":" not in address:
        print(f"[monitor] TCP check address '{address}' is missing a port "
              f"(expected format: host:port, e.g. 192.168.1.10:3306)")
        return False, None

    host, _, port_str = address.rpartition(":")
    try:
        port = int(port_str)
    except ValueError:
        print(f"[monitor] TCP check address '{address}' has an invalid port")
        return False, None

    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            elapsed_ms = (time.time() - start) * 1000
            return True, round(elapsed_ms, 1)
    except (socket.timeout, OSError):
        return False, None


def dns_check(hostname: str, timeout_seconds: int = 3):
    """
    Check whether a hostname resolves via DNS. Useful for confirming
    DNS itself is working/reachable, separate from whether the host
    behind it responds to ping or a port.

    Returns (is_up: bool, response_time_ms: float | None)
    """
    start = time.time()
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_seconds)
    try:
        socket.gethostbyname(hostname)
        elapsed_ms = (time.time() - start) * 1000
        return True, round(elapsed_ms, 1)
    except (socket.gaierror, socket.timeout):
        return False, None
    finally:
        socket.setdefaulttimeout(old_timeout)


def database_check(database_path: str):
    """Run a lightweight integrity query against a SQLite database file."""
    start = time.time()
    try:
        conn = sqlite3.connect(database_path.strip(), timeout=3)
        result = conn.execute("PRAGMA quick_check").fetchone()
        conn.close()
        elapsed_ms = (time.time() - start) * 1000
        return result == ("ok",), round(elapsed_ms, 1) if result == ("ok",) else None
    except (OSError, sqlite3.Error):
        return False, None


def check_server(address: str, check_type: str):
    """Dispatch to the right check function based on check_type."""
    if check_type in {"http", "api"}:
        return http_check(address)
    if check_type == "tcp":
        return tcp_check(address)
    if check_type == "dns":
        return dns_check(address)
    if check_type == "database":
        return database_check(address)
    return ping_host(address)


def log_check(server_id: int, is_up: bool, response_time_ms):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO checks (server_id, timestamp, is_up, response_time_ms)
        VALUES (?, ?, ?, ?)
        """,
        (
            server_id,
            datetime.now(timezone.utc).isoformat(),
            1 if is_up else 0,
            response_time_ms,
        ),
    )
    conn.commit()
    conn.close()


def open_incident(server_id: int, started_at: str):
    """Start a new incident record (server just went down)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO incidents (server_id, started_at, ended_at) VALUES (?, ?, NULL)",
        (server_id, started_at),
    )
    conn.commit()
    conn.close()


def close_incident(server_id: int, ended_at: str):
    """Close the most recent open incident for this server (it recovered)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE incidents
        SET ended_at = ?
        WHERE id = (
            SELECT id FROM incidents
            WHERE server_id = ? AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
        )
        """,
        (ended_at, server_id),
    )
    conn.commit()
    conn.close()


def get_open_incidents():
    """Return all currently-open incidents (server_id, incident_id, started_at)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, server_id, started_at FROM incidents WHERE ended_at IS NULL")
    rows = cur.fetchall()
    conn.close()
    return rows


def close_incident_with_note(incident_id: int, ended_at: str, note: str):
    """Close a specific incident by id, with an explanatory note."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE incidents SET ended_at = ?, note = ? WHERE id = ?",
        (ended_at, note, incident_id),
    )
    conn.commit()
    conn.close()


def run_once():
    """Check all active servers one time and log the results."""
    servers = get_active_servers()
    if not servers:
        print("No servers configured yet. Add some with add_server().")
        return

    for server_id, name, address, check_type, alert_threshold, maintenance_mode in servers:
        is_up, response_time_ms = check_server(address, check_type)
        log_check(server_id, is_up, response_time_ms)
        status = "UP" if is_up else "DOWN"
        rt = f"{response_time_ms} ms" if response_time_ms else "-"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {name} ({address}, {check_type}): {status} ({rt})")

        if maintenance_mode:
            _confirmed_status.pop(server_id, None)
            _consecutive_failures.pop(server_id, None)
            print(f"[monitor] {name} is in maintenance mode; alerts and incidents suppressed")
            continue

        # Threshold-aware state machine: a server is only considered
        # "confirmed down" (triggering an alert + incident) after
        # `alert_threshold` consecutive failed checks — this filters
        # out brief one-off network blips. Recovery is immediate on
        # the first successful check after being confirmed down,
        # since "is it back up" doesn't need the same caution as
        # "is it really down".
        previously_confirmed = _confirmed_status.get(server_id)
        now = datetime.now(timezone.utc).isoformat()

        if is_up:
            _consecutive_failures[server_id] = 0
            if previously_confirmed is False:
                close_incident(server_id, now)
                alerts.notify_status_change(name, address, True)
            _confirmed_status[server_id] = True
        else:
            _consecutive_failures[server_id] = _consecutive_failures.get(server_id, 0) + 1
            fails = _consecutive_failures[server_id]
            if previously_confirmed is not False and fails >= alert_threshold:
                if fails > 1:
                    print(f"[monitor] {name} confirmed DOWN after {fails} consecutive failed checks "
                          f"(threshold: {alert_threshold})")
                open_incident(server_id, now)
                alerts.notify_status_change(name, address, False)
                _confirmed_status[server_id] = False
            elif previously_confirmed is None:
                # First time seeing this server and it's already down —
                # don't alert yet, just start counting (matches the
                # "no alert on the very first check" behavior from before).
                pass


def handle_monitoring_gap(gap_seconds: float, resumed_at: str):
    """
    Called when we detect the monitor was paused for much longer than
    the normal check interval (e.g. the machine went to sleep, or the
    script was stopped and restarted after a while). Without this,
    an incident that was open when the pause started would end up
    reporting the entire sleep/pause duration as "downtime", which is
    misleading — the target may have been fine the whole time; we
    just weren't checking it.

    Closes any currently-open incidents with a clear note instead of
    letting their duration silently include the gap, and clears the
    in-memory status tracking so the next check doesn't fire a false
    alert based on stale pre-gap state.
    """
    gap_minutes = round(gap_seconds / 60, 1)
    print(f"[monitor] Detected a {gap_minutes} min gap since the last check "
          f"(likely sleep/pause) — closing any open incidents with a note "
          f"instead of counting the gap as downtime.")

    note = f"Monitor was offline for ~{gap_minutes} min (sleep/pause) — duration may be inaccurate."
    for incident_id, _, _ in get_open_incidents():
        close_incident_with_note(incident_id, resumed_at, note)

    _confirmed_status.clear()
    _consecutive_failures.clear()


def get_last_check_timestamp():
    """Return the timestamp (ISO string) of the most recent check ever logged, or None."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT MAX(timestamp) FROM checks")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def run_forever():
    init_db()
    print(f"Starting uptime monitor. Checking every {CHECK_INTERVAL_SECONDS}s. Ctrl+C to stop.")

    last_run_time = None
    # If a gap is more than 3x the normal interval, treat it as a
    # pause/sleep event rather than normal timing jitter.
    gap_threshold_seconds = CHECK_INTERVAL_SECONDS * 3

    # Also check for a gap that happened while the script itself wasn't
    # running at all (e.g. you closed the terminal last night and
    # reopened it this morning) — not just a mid-loop sleep/pause.
    last_check_iso = get_last_check_timestamp()
    if last_check_iso:
        last_check_time = datetime.fromisoformat(last_check_iso)
        startup_gap = (datetime.now(timezone.utc) - last_check_time).total_seconds()
        if startup_gap > gap_threshold_seconds:
            handle_monitoring_gap(startup_gap, datetime.now(timezone.utc).isoformat())

    while True:
        now = time.time()
        if last_run_time is not None:
            gap = now - last_run_time
            if gap > gap_threshold_seconds:
                handle_monitoring_gap(gap, datetime.now(timezone.utc).isoformat())
        last_run_time = now

        run_once()
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    init_db()

    # No example servers are auto-added anymore — with multi-tenancy,
    # servers belong to a specific user account, so add yours from the
    # dashboard after logging in (Add a server, in the sidebar).
    run_forever()
