"""
One-off fix: directly add the alert_threshold column if it's missing,
in case the automatic migration in monitor.py's init_db() didn't run
(e.g. an older monitor.py file got saved by mistake).

Run with:
    python fix_schema.py
"""

import sqlite3

conn = sqlite3.connect("uptime.db")
cur = conn.cursor()

cur.execute("PRAGMA table_info(servers)")
existing_columns = [row[1] for row in cur.fetchall()]

if "alert_threshold" in existing_columns:
    print("alert_threshold column already exists — nothing to do.")
else:
    cur.execute("ALTER TABLE servers ADD COLUMN alert_threshold INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    print("Added alert_threshold column (default 1) to the servers table.")

conn.close()
