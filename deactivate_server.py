"""
One-off utility: deactivate a server by address without deleting its
history. Run with:
    python deactivate_server.py 192.0.2.1
"""

import sqlite3
import sys

if len(sys.argv) != 2:
    print("Usage: python deactivate_server.py <address>")
    sys.exit(1)

address = sys.argv[1]

conn = sqlite3.connect("uptime.db")
cur = conn.cursor()
cur.execute("UPDATE servers SET active = 0 WHERE address = ?", (address,))
conn.commit()
rows_changed = cur.rowcount
conn.close()

if rows_changed:
    print(f"Deactivated server with address: {address}")
else:
    print(f"No server found with address: {address}")