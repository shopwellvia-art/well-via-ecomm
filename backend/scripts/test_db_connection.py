"""Diagnose the MySQL connection the app uses.

Layered checks so a failure tells you *where* it broke, not just "couldn't
connect":

  1. Show the resolved connection target (password masked).
  2. Raw TCP reach to host:port  → separates "host/network unreachable" from
     "auth / wrong database".
  3. SQLAlchemy + PyMySQL connect (exact same URL/driver as the app), run
     SELECT 1, and report the server version + current database.

Run it inside the backend container so it uses the real .env + installed deps:

    docker compose exec backend python scripts/test_db_connection.py

Exits 0 on success, 1 on any failure.
"""
from __future__ import annotations

import os
import socket
import sys

CONNECT_TIMEOUT = 5  # seconds — fail fast instead of hanging on a dead host


def _target() -> tuple[str, int, str, str, str, str]:
    """Resolve (host, port, user, password, db, url). Prefer the app's own
    settings so this matches production exactly; fall back to raw env vars."""
    try:
        from app.core.config import settings  # noqa: PLC0415

        return (
            settings.MYSQL_HOST,
            settings.MYSQL_PORT,
            settings.MYSQL_USER,
            settings.MYSQL_PASSWORD,
            settings.MYSQL_DB,
            settings.DATABASE_URL,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  (could not import app settings: {exc}; falling back to env vars)")
        from urllib.parse import quote_plus  # noqa: PLC0415

        host = os.environ.get("MYSQL_HOST", "")
        port = int(os.environ.get("MYSQL_PORT", "3306"))
        user = os.environ.get("MYSQL_USER", "")
        password = os.environ.get("MYSQL_PASSWORD", "")
        db = os.environ.get("MYSQL_DB", "")
        url = (
            f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{db}"
        )
        return host, port, user, password, db, url


def _mask(url: str, password: str) -> str:
    return url.replace(password, "***") if password else url


def main() -> int:
    host, port, user, password, db, url = _target()

    print("=" * 60)
    print("MySQL connection diagnostic")
    print("=" * 60)
    print(f"host : {host}")
    print(f"port : {port}")
    print(f"user : {user}")
    print(f"db   : {db}")
    print(f"url  : {_mask(url, password)}")
    print("-" * 60)

    # ---- Step 1: raw TCP reachability ----
    print(f"[1/2] TCP connect to {host}:{port} (timeout {CONNECT_TIMEOUT}s)...")
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT):
            print("      OK — port is open and accepting TCP connections.")
    except socket.gaierror as exc:
        print(f"      FAIL — DNS / hostname could not be resolved: {exc}")
        print("      → Check MYSQL_HOST is correct and resolvable from here.")
        return 1
    except (TimeoutError, OSError) as exc:
        print(f"      FAIL — could not reach the port: {exc}")
        print("      → MySQL may be down, the port wrong, or a firewall /")
        print("        AWS security group is blocking this machine's IP.")
        return 1

    # ---- Step 2: real driver connect + query ----
    print("[2/2] SQLAlchemy + PyMySQL connect, SELECT 1...")
    try:
        from sqlalchemy import create_engine, text  # noqa: PLC0415

        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": CONNECT_TIMEOUT},
        )
        with engine.connect() as conn:
            one = conn.execute(text("SELECT 1")).scalar()
            version = conn.execute(text("SELECT VERSION()")).scalar()
            current_db = conn.execute(text("SELECT DATABASE()")).scalar()
            tables = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = :db"
                ),
                {"db": db},
            ).scalar()
        engine.dispose()
        print(f"      OK — SELECT 1 returned {one}")
        print(f"      server version : {version}")
        print(f"      current db     : {current_db}")
        print(f"      tables in db   : {tables}")
        print("-" * 60)
        print("RESULT: ✅  Database connection is working.")
        return 0
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        print(f"      FAIL — {type(exc).__name__}: {msg}")
        print("-" * 60)
        # Map the common MySQL error codes to a plain-English cause.
        if "1045" in msg or "Access denied" in msg:
            print("CAUSE: wrong username or password (auth rejected by MySQL).")
        elif "1049" in msg or "Unknown database" in msg:
            print(f"CAUSE: the database '{db}' does not exist on the server.")
        elif "2003" in msg or "Can't connect" in msg:
            print("CAUSE: MySQL not reachable (down / wrong host:port / firewall).")
        elif "1130" in msg or "not allowed to connect" in msg:
            print(
                f"CAUSE: host '{host}' refuses connections from this machine's IP "
                "(MySQL user not granted for this source host / GRANT scope)."
            )
        elif "timeout" in msg.lower():
            print("CAUSE: connection timed out — network path blocked or host slow.")
        print("RESULT: ❌  Database connection FAILED.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
