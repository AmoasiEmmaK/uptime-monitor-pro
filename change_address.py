"""
One-off utility: change a server's address by name. Used for testing
alerts by switching a working server to a dead address, so monitor.py
sees a real UP -> DOWN transition (and fires an alert).

Run with:
    python change_address.py "Test Alert" 192.0.2.1
"""

import sqlite3
import sys

if len(sys.argv) != 3:
    print('Usage: python change_address.py "<server name>" <new address>')
    sys.exit(1)

name = sys.argv[1]
new_address = sys.argv[2]

conn = sqlite3.connect("uptime.db")
cur = conn.cursor()
cur.execute("UPDATE servers SET address = ? WHERE name = ?", (new_address, name))
conn.commit()
rows_changed = cur.rowcount
conn.close()

if rows_changed:
    print(f"Changed '{name}' to address: {new_address}")
else:
    print(f"No server found with name: {name}")
