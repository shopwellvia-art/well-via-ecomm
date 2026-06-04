"""One-shot connectivity check against the configured MySQL DSN."""
import sys
import pymysql

HOST = "3.110.31.141"
PORT = 3306
USER = "vinay"
PASSWORD = "Vinay@1234#"
DB = "ecommercesimple"

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