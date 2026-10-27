"""
Diagnostic: print every server's exact stored values, including
alert_threshold, to debug why threshold-based alerting might not be
firing as expected.

Run with:
    python check_thresholds.py
"""

import sqlite3

from monitor import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("SELECT id, name, address, check_type, active, user_id, alert_threshold FROM servers")
rows = cur.fetchall()
conn.close()

if not rows:
    print("No servers in the database.")
else:
    print(f"{'ID':<4} {'Name':<20} {'Address':<20} {'Type':<6} {'Active':<7} {'UserID':<7} {'Threshold'}")
    for id_, name, address, check_type, active, user_id, alert_threshold in rows:
        print(f"{id_:<4} {name:<20} {repr(address):<20} {check_type:<6} {active:<7} {str(user_id):<7} {alert_threshold}")
