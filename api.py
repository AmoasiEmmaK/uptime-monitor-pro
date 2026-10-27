"""
UptimeMonitor web API.

A small Flask backend that sits in front of the same uptime.db that
monitor.py writes to, and serves the web dashboard in web/index.html.

Run with:
    python api.py

Then open http://127.0.0.1:5000 in your browser.

Keep monitor.py running separately in another terminal — that's what
actually performs the checks and writes to the database. This API only
reads that data and manages the server list.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, session, send_from_directory

from monitor import (
    DB_PATH,
    init_db,
    add_server,
    update_server,
    set_server_active,
    delete_server_permanently,
    check_server_now,
)

try:
    from auth import (
        init_auth_db,
        create_user,
        verify_user,
        get_user_id,
        get_user_role,
        is_locked_out,
        record_login_attempt,
        record_audit,
    )
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Could not import auth.py. Put api.py in the same folder as "
        "monitor.py, dashboard.py and auth.py.\n"
        f"Original error: {exc}"
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# The standalone frontend lives beside api.py in this project.
WEB_DIR = BASE_DIR
SECRET_PATH = os.path.join(BASE_DIR, ".flask_secret")

# Response time (ms) at or above which an "up" service is shown as slow.
# Matches the threshold the Streamlit dashboard uses.
DEGRADED_MS = 1000

# How many recent checks each service card shows in its status strip.
STRIP_LENGTH = 48

VALID_CHECK_TYPES = {"ping", "http", "api", "tcp", "dns", "database"}


def load_secret_key() -> bytes:
    """
    Load (or create) a persistent secret key for signing session cookies.

    Persisting it means restarting the API doesn't log everyone out.
    """
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "rb") as handle:
            return handle.read()
    key = secrets.token_bytes(32)
    with open(SECRET_PATH, "wb") as handle:
        handle.write(key)
    try:
        os.chmod(SECRET_PATH, 0o600)
    except OSError:
        pass  # Windows may not support this; not fatal
    return key


app = Flask(__name__, static_folder=None)
app.secret_key = load_secret_key()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True

init_db()
init_auth_db()


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def current_user_id():
    return session.get("user_id")


def require_login():
    """Return None if logged in, or a (response, status) tuple if not."""
    if not current_user_id():
        return jsonify({"error": "Not signed in."}), 401
    return None


def cutoff_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def uptime_stats(conn, server_id: int, hours: float):
    """Return (uptime_percent, total_checks, failed_checks, avg_response_ms)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS total,
               SUM(is_up) AS up_count,
               AVG(response_time_ms) AS avg_ms
        FROM checks
        WHERE server_id = ? AND timestamp >= ?
        """,
        (server_id, cutoff_iso(hours)),
    ).fetchone()

    total = row["total"] or 0
    if not total:
        return None, 0, 0, None

    up_count = row["up_count"] or 0
    percent = round(100 * up_count / total, 2)
    avg_ms = round(row["avg_ms"], 1) if row["avg_ms"] is not None else None
    return percent, total, total - up_count, avg_ms


def recent_strip(conn, server_id: int, limit: int = STRIP_LENGTH):
    """Most recent checks, oldest first, for the status strip and sparkline."""
    rows = conn.execute(
        """
        SELECT timestamp, is_up, response_time_ms
        FROM checks
        WHERE server_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (server_id, limit),
    ).fetchall()
    return [
        {
            "timestamp": row["timestamp"],
            "is_up": bool(row["is_up"]),
            "response_time_ms": row["response_time_ms"],
        }
        for row in reversed(rows)
    ]


def history_series(conn, server_id: int, hours: float, max_points: int = 300):
    """Checks within the window, oldest first, thinned to max_points."""
    rows = conn.execute(
        """
        SELECT timestamp, is_up, response_time_ms
        FROM checks
        WHERE server_id = ? AND timestamp >= ?
        ORDER BY timestamp
        """,
        (server_id, cutoff_iso(hours)),
    ).fetchall()

    if len(rows) > max_points:
        step = len(rows) / max_points
        rows = [rows[int(i * step)] for i in range(max_points)]

    return [
        {
            "timestamp": row["timestamp"],
            "is_up": bool(row["is_up"]),
            "response_time_ms": row["response_time_ms"],
        }
        for row in rows
    ]


def service_state(row, latest):
    """Resolve a service to one of: maintenance / pending / down / slow / up."""
    if row["maintenance_mode"]:
        return "maintenance"
    if latest is None or latest["is_up"] is None:
        return "pending"
    if not latest["is_up"]:
        return "down"
    ms = latest["response_time_ms"]
    if ms is not None and ms >= DEGRADED_MS:
        return "slow"
    return "up"


# --------------------------------------------------------------------
# Auth routes
# --------------------------------------------------------------------

@app.post("/api/signup")
def signup():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    success, message = create_user(email, password)
    if not success:
        return jsonify({"error": message}), 400
    return jsonify({"message": message})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""

    locked, minutes_remaining = is_locked_out(email)
    if locked:
        return jsonify({
            "error": f"Too many failed attempts. Try again in about "
                     f"{minutes_remaining} minute(s)."
        }), 429

    success, message = verify_user(email, password)
    record_login_attempt(email, success)
    if not success:
        return jsonify({"error": message}), 401

    user_id = get_user_id(email)
    session.clear()
    session["user_id"] = user_id
    session["email"] = email.lower()
    session.permanent = True
    record_audit(user_id, "login_success", email.lower())

    return jsonify({
        "email": email.lower(),
        "role": get_user_role(user_id) or "user",
    })


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"message": "Signed out."})


@app.get("/api/me")
def me():
    user_id = current_user_id()
    if not user_id:
        return jsonify({"signed_in": False})
    return jsonify({
        "signed_in": True,
        "email": session.get("email", ""),
        "role": get_user_role(user_id) or "user",
    })


# --------------------------------------------------------------------
# Dashboard data
# --------------------------------------------------------------------

@app.get("/api/dashboard")
def dashboard():
    guard = require_login()
    if guard:
        return guard

    user_id = current_user_id()
    try:
        hours = float(request.args.get("hours", 24))
    except ValueError:
        hours = 24.0
    hours = max(1.0, min(hours, 24 * 90))

    conn = get_connection()

    server_rows = conn.execute(
        """
        SELECT id, name, address, check_type, active,
               alert_threshold, maintenance_mode
        FROM servers
        WHERE user_id = ?
        ORDER BY name COLLATE NOCASE
        """,
        (user_id,),
    ).fetchall()

    services = []
    for row in server_rows:
        latest_row = conn.execute(
            """
            SELECT timestamp, is_up, response_time_ms
            FROM checks
            WHERE server_id = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()

        latest = None
        if latest_row is not None:
            latest = {
                "timestamp": latest_row["timestamp"],
                "is_up": bool(latest_row["is_up"]),
                "response_time_ms": latest_row["response_time_ms"],
            }

        window_uptime, window_total, window_failed, window_avg = uptime_stats(
            conn, row["id"], hours
        )
        uptime_24h, _, failed_24h, avg_24h = uptime_stats(conn, row["id"], 24)
        uptime_7d, _, _, _ = uptime_stats(conn, row["id"], 24 * 7)
        uptime_30d, _, _, _ = uptime_stats(conn, row["id"], 24 * 30)

        services.append({
            "id": row["id"],
            "name": row["name"],
            "address": row["address"],
            "check_type": row["check_type"],
            "active": bool(row["active"]),
            "alert_threshold": row["alert_threshold"],
            "maintenance_mode": bool(row["maintenance_mode"]),
            "latest": latest,
            "state": service_state(row, latest) if row["active"] else "paused",
            "strip": recent_strip(conn, row["id"]),
            "history": history_series(conn, row["id"], hours),
            "window": {
                "uptime": window_uptime,
                "checks": window_total,
                "failed": window_failed,
                "avg_response_ms": window_avg,
            },
            "uptime_24h": uptime_24h,
            "uptime_7d": uptime_7d,
            "uptime_30d": uptime_30d,
            "failed_24h": failed_24h,
            "avg_response_24h": avg_24h,
        })

    incident_rows = conn.execute(
        """
        SELECT i.id, i.started_at, i.ended_at, i.note,
               s.name, s.address
        FROM incidents i
        JOIN servers s ON s.id = i.server_id
        WHERE s.user_id = ?
        ORDER BY i.started_at DESC
        LIMIT 50
        """,
        (user_id,),
    ).fetchall()

    incidents = [
        {
            "id": row["id"],
            "name": row["name"],
            "address": row["address"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "note": row["note"],
        }
        for row in incident_rows
    ]

    check_rows = conn.execute(
        """
        SELECT s.name, s.check_type, c.timestamp, c.is_up, c.response_time_ms
        FROM checks c
        JOIN servers s ON s.id = c.server_id
        WHERE s.user_id = ?
        ORDER BY c.timestamp DESC
        LIMIT 40
        """,
        (user_id,),
    ).fetchall()

    recent_checks = [
        {
            "name": row["name"],
            "check_type": row["check_type"],
            "timestamp": row["timestamp"],
            "is_up": bool(row["is_up"]),
            "response_time_ms": row["response_time_ms"],
        }
        for row in check_rows
    ]

    last_check_row = conn.execute("SELECT MAX(timestamp) AS last FROM checks").fetchone()
    conn.close()

    active = [s for s in services if s["active"]]
    counts = {
        "total": len(services),
        "active": len(active),
        "up": sum(1 for s in active if s["state"] == "up"),
        "slow": sum(1 for s in active if s["state"] == "slow"),
        "down": sum(1 for s in active if s["state"] == "down"),
        "maintenance": sum(1 for s in active if s["state"] == "maintenance"),
        "pending": sum(1 for s in active if s["state"] == "pending"),
        "paused": sum(1 for s in services if not s["active"]),
    }

    counted = [s["window"]["uptime"] for s in active if s["window"]["uptime"] is not None]
    counts["overall_uptime"] = round(sum(counted) / len(counted), 2) if counted else None

    return jsonify({
        "window_hours": hours,
        "degraded_ms": DEGRADED_MS,
        "services": services,
        "incidents": incidents,
        "recent_checks": recent_checks,
        "summary": counts,
        "last_check_at": last_check_row["last"] if last_check_row else None,
        "server_time": datetime.now(timezone.utc).isoformat(),
    })


# --------------------------------------------------------------------
# Server management
# --------------------------------------------------------------------

def validated_payload(data):
    """Return (values, error_message). Shared by create and update."""
    name = (data.get("name") or "").strip()
    address = (data.get("address") or "").strip()
    check_type = (data.get("check_type") or "ping").strip().lower()

    if not name:
        return None, "Give the service a name."
    if not address:
        return None, "Enter a target address."
    if check_type not in VALID_CHECK_TYPES:
        return None, f"'{check_type}' isn't a check type this monitor knows."
    if check_type == "tcp" and ":" not in address:
        return None, "A TCP check needs a port, like 192.168.1.10:3306."
    if check_type in {"http", "api"} and not address.lower().startswith(("http://", "https://")):
        return None, "An HTTP check needs a full URL starting with http:// or https://."

    try:
        threshold = int(data.get("alert_threshold", 1))
    except (TypeError, ValueError):
        return None, "Alert threshold must be a whole number."
    threshold = max(1, min(threshold, 10))

    return {
        "name": name,
        "address": address,
        "check_type": check_type,
        "alert_threshold": threshold,
        "maintenance_mode": bool(data.get("maintenance_mode", False)),
    }, None


@app.post("/api/servers")
def create_server():
    guard = require_login()
    if guard:
        return guard

    values, error = validated_payload(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400

    add_server(
        values["name"],
        values["address"],
        values["check_type"],
        current_user_id(),
        values["alert_threshold"],
    )
    record_audit(current_user_id(), "server_added", values["name"])
    return jsonify({"message": f"Now monitoring {values['name']}."}), 201


@app.put("/api/servers/<int:server_id>")
def edit_server(server_id):
    guard = require_login()
    if guard:
        return guard

    values, error = validated_payload(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400

    update_server(
        server_id,
        values["name"],
        values["address"],
        values["check_type"],
        current_user_id(),
        values["alert_threshold"],
        values["maintenance_mode"],
    )
    record_audit(current_user_id(), "server_updated", values["name"])
    return jsonify({"message": f"Saved changes to {values['name']}."})


@app.post("/api/servers/<int:server_id>/active")
def toggle_active(server_id):
    guard = require_login()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    active = bool(data.get("active", True))
    set_server_active(server_id, active, current_user_id())
    record_audit(current_user_id(), "server_resumed" if active else "server_paused", str(server_id))
    return jsonify({
        "message": "Monitoring resumed." if active else "Monitoring paused. History is kept."
    })


@app.delete("/api/servers/<int:server_id>")
def remove_server(server_id):
    guard = require_login()
    if guard:
        return guard

    delete_server_permanently(server_id, current_user_id())
    record_audit(current_user_id(), "server_deleted", str(server_id))
    return jsonify({"message": "Service and its history deleted."})


@app.post("/api/servers/<int:server_id>/check")
def check_now(server_id):
    guard = require_login()
    if guard:
        return guard

    result = check_server_now(server_id, current_user_id())
    if result is None:
        return jsonify({"error": "That service isn't on your account."}), 404

    is_up, response_time_ms = result
    return jsonify({"is_up": bool(is_up), "response_time_ms": response_time_ms})


@app.post("/api/check-all")
def check_all():
    guard = require_login()
    if guard:
        return guard

    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM servers WHERE user_id = ? AND active = 1", (current_user_id(),)
    ).fetchall()
    conn.close()

    for row in rows:
        check_server_now(row["id"], current_user_id())

    return jsonify({"checked": len(rows)})


# --------------------------------------------------------------------
# Static front end
# --------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(WEB_DIR, filename)


if __name__ == "__main__":
    print("UptimeMonitor web API running at http://127.0.0.1:5000")
    print("Remember to run monitor.py in a separate terminal so checks keep logging.")
    app.run(host="127.0.0.1", port=5000, debug=True)
