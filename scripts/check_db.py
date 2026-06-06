"""One-shot connectivity check against the configured MySQL DSN.

All connection parameters are read from environment variables (or a loaded .env file).
Set MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB before running.

SECURITY NOTE: A previous version of this file hardcoded the live DB host and
credentials directly in source code. Those literals have been removed. The
previously committed credential (host, user, password) must be treated as
compromised and rotated immediately, and the git history must be purged
(e.g. via git-filter-repo or BFG).
"""
import os
import sys
import pymysql

HOST = os.environ["MYSQL_HOST"]
PORT = int(os.environ.get("MYSQL_PORT", 3306))
USER = os.environ["MYSQL_USER"]
PASSWORD = os.environ["MYSQL_PASSWORD"]
DB = os.environ["MYSQL_DB"]

try:
    conn = pymysql.connect(
        host=HOST, port=PORT, user=USER, password=PASSWORD,
        database=DB, connect_timeout=10,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT VERSION(), DATABASE(), CURRENT_USER()")
        version, db, user = cur.fetchone()
        cur.execute("SHOW TABLES")
        tables = [row[0] for row in cur.fetchall()]
    conn.close()
    print(f"OK  version={version}  db={db}  user={user}  tables={len(tables)}")
    if tables:
        print("    " + ", ".join(tables))
except pymysql.err.OperationalError as e:
    print(f"FAIL  OperationalError: {e}")
    sys.exit(1)
except Exception as e:
    print(f"FAIL  {type(e).__name__}: {e}")
    sys.exit(1)
