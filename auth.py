"""
Authentication module — user signup and login, using only the
standard library (hashlib's PBKDF2 implementation) so there's no
extra dependency to install.

This is a starting point for a real product's auth, not a finished
security system. In particular:
- No password reset flow yet
- No email verification
- No rate limiting on login attempts (a real product needs this to
  resist brute-force attempts)
- Single flat user table, not yet scoped to separate companies/teams
  (that's the multi-tenancy step, which comes later)
"""

import sqlite3
import hashlib
import os
import binascii
import secrets
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uptime.db")

# PBKDF2 iteration count — higher is slower but more resistant to
# brute-force attacks. 260,000 is roughly OWASP's current guidance
# for PBKDF2-SHA256 as of last update; safe to leave as-is.
PBKDF2_ITERATIONS = 260_000


def init_auth_db():
    """Create the users and login_attempts tables if they don't exist yet."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    columns = [row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "role" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL DEFAULT 'API token',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            success INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# Lockout policy: after this many failed attempts within the window,
# further login attempts for that email are blocked until the window
# passes. This protects against automated password-guessing.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_MINUTES = 15


def is_locked_out(email: str) -> tuple[bool, int]:
    """
    Check if an email is currently locked out due to too many recent
    failed attempts. Returns (is_locked_out, minutes_remaining).
    """
    email = email.strip().lower()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)).isoformat()
    cur.execute(
        """
        SELECT attempted_at FROM login_attempts
        WHERE email = ? AND success = 0 AND attempted_at >= ?
        ORDER BY attempted_at ASC
        """,
        (email, cutoff),
    )
    rows = cur.fetchall()
    conn.close()

    if len(rows) < MAX_FAILED_ATTEMPTS:
        return False, 0

    oldest_relevant_attempt = datetime.fromisoformat(rows[-MAX_FAILED_ATTEMPTS][0])
    unlock_time = oldest_relevant_attempt + timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
    remaining = (unlock_time - datetime.now(timezone.utc)).total_seconds() / 60
    if remaining <= 0:
        return False, 0
    return True, max(1, round(remaining))


def record_login_attempt(email: str, success: bool):
    email = email.strip().lower()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO login_attempts (email, attempted_at, success) VALUES (?, ?, ?)",
        (email, datetime.now(timezone.utc).isoformat(), 1 if success else 0),
    )
    conn.commit()
    conn.close()


def _hash_password(password: str, salt: bytes) -> str:
    """Hash a password with the given salt using PBKDF2-SHA256."""
    hashed = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS
    )
    return binascii.hexlify(hashed).decode("utf-8")


def create_user(email: str, password: str) -> tuple[bool, str]:
    """
    Create a new user account.
    Returns (success: bool, message: str).
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        return False, "Please enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."

    salt = os.urandom(16)
    password_hash = _hash_password(password, salt)
    salt_hex = binascii.hexlify(salt).decode("utf-8")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, password_hash, salt, role) VALUES (?, ?, ?, ?)",
            (email, password_hash, salt_hex, "user"),
        )
        conn.commit()
        return True, "Account created."
    except sqlite3.IntegrityError:
        return False, "An account with that email already exists."
    finally:
        conn.close()


def verify_user(email: str, password: str) -> tuple[bool, str]:
    """
    Check an email/password combination against stored credentials.
    Returns (success: bool, message: str).
    """
    email = email.strip().lower()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT password_hash, salt FROM users WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, "No account found with that email."

    stored_hash, salt_hex = row
    salt = binascii.unhexlify(salt_hex)
    computed_hash = _hash_password(password, salt)

    if computed_hash == stored_hash:
        return True, "Logged in."
    return False, "Incorrect password."


def get_user_id(email: str):
    """Look up a user's id by email. Returns None if not found."""
    email = email.strip().lower()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE email = ?", (email,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_user_role(user_id: int) -> str | None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def record_audit(user_id: int | None, action: str, details: str = ""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO audit_logs (user_id, action, details) VALUES (?, ?, ?)",
        (user_id, action, details),
    )
    conn.commit()
    conn.close()


def create_api_token(user_id: int, label: str = "API token") -> str:
    """Create a bearer token; only its hash is stored in SQLite."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO api_tokens (user_id, token_hash, label) VALUES (?, ?, ?)",
        (user_id, token_hash, label.strip() or "API token"),
    )
    conn.commit()
    conn.close()
    record_audit(user_id, "api_token_created", label)
    return token


def authenticate_api_token(token: str) -> int | None:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT user_id FROM api_tokens WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE api_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE token_hash = ?",
            (token_hash,),
        )
        conn.commit()
    conn.close()
    return row[0] if row else None
