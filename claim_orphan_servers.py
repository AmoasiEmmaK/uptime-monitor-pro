"""
One-off utility: assign all servers with no owner (user_id IS NULL —
i.e. added before multi-tenancy existed) to a specific user account,
identified by email. Run this once after adding auth, so your
existing test servers (Google DNS, Cloudflare DNS, etc.) show up
under your account instead of disappearing.

Run with:
    python claim_orphan_servers.py youremail@example.com
"""

import sqlite3
import sys

if len(sys.argv) != 2:
    print("Usage: python claim_orphan_servers.py <your account email>")
    sys.exit(1)

email = sys.argv[1].strip().lower()

conn = sqlite3.connect("uptime.db")
cur = conn.cursor()

cur.execute("SELECT id FROM users WHERE email = ?", (email,))
row = cur.fetchone()
if not row:
    print(f"No account found with email: {email}. Create an account first via the dashboard.")
    conn.close()
    sys.exit(1)

user_id = row[0]
cur.execute("UPDATE servers SET user_id = ? WHERE user_id IS NULL", (user_id,))
count = cur.rowcount
conn.commit()
conn.close()

print(f"Assigned {count} ownerless server(s) to {email}.")
