"""Diagnose the MySQL connection used by the backend.

The script reads MYSQL_* and DB_* values from the environment first, then falls
back to backend/.env and the repository-level .env. It never hardcodes or prints
the database password.

Examples:
    cd backend
    python scripts/test_db_connection.py

    # With explicit env vars:
    MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=ecom \
    MYSQL_PASSWORD=secret MYSQL_DB=ecommerce python scripts/test_db_connection.py

    # Optional pool exercise. This opens several simultaneous connections.
    python scripts/test_db_connection.py --pool-check --pool-check-size 10
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from dataclasses import dataclass
from urllib.parse import quote_plus


BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_ROOT)

if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    user: str
    password: str
    database: str
    pool_size: int
    max_overflow: int
    url: str
    source: str


def _load_env_file(path: str) -> None:
    """Load simple KEY=VALUE lines without overriding existing env vars."""
    try:
        with open(path, encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        return


def _load_dotenvs() -> None:
    _load_env_file(os.path.join(BACKEND_ROOT, ".env"))
    _load_env_file(os.path.join(REPO_ROOT, ".env"))


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _build_url(user: str, password: str, host: str, port: int, database: str) -> str:
    return (
        f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{database}"
    )


def _target_from_env(settings_error: Exception | None = None) -> Target:
    missing = [
        name
        for name in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB")
        if not os.environ.get(name)
    ]
    if missing:
        details = f" Missing required env vars: {', '.join(missing)}."
        if settings_error is not None:
            details += f" App settings could not be loaded: {settings_error}"
        raise RuntimeError(details)

    host = os.environ["MYSQL_HOST"]
    port = _env_int("MYSQL_PORT", 3306)
    user = os.environ["MYSQL_USER"]
    password = os.environ["MYSQL_PASSWORD"]
    database = os.environ["MYSQL_DB"]
    pool_size = _env_int("DB_POOL_SIZE", 10)
    max_overflow = _env_int("DB_MAX_OVERFLOW", 20)
    return Target(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        pool_size=pool_size,
        max_overflow=max_overflow,
        url=_build_url(user, password, host, port, database),
        source="environment/.env fallback",
    )


def _resolve_target() -> Target:
    """Prefer app settings so the URL construction matches production."""
    try:
        from app.core.config import settings  # noqa: PLC0415

        return Target(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD,
            database=settings.MYSQL_DB,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            url=settings.DATABASE_URL,
            source="app.core.config.settings",
        )
    except Exception as exc:  # noqa: BLE001
        return _target_from_env(settings_error=exc)


def _masked_url(url: str, password: str) -> str:
    masked = url
    for token in {password, quote_plus(password)}:
        if token:
            masked = masked.replace(token, "***")
    return masked


def _tcp_check(target: Target, timeout: float) -> bool:
    print(f"[1/2] TCP connect to {target.host}:{target.port} (timeout {timeout}s)")
    try:
        with socket.create_connection((target.host, target.port), timeout=timeout):
            print("      OK: TCP port is reachable.")
            return True
    except socket.gaierror as exc:
        print(f"      FAIL: host name could not be resolved: {exc}")
    except (TimeoutError, OSError) as exc:
        print(f"      FAIL: TCP connection failed: {exc}")

    print("      Check host, port, MySQL status, firewall, and AWS security group rules.")
    return False


def _sql_check(target: Target, timeout: float, pool_check_size: int | None) -> bool:
    print("[2/2] SQLAlchemy + PyMySQL connect and query")
    try:
        from sqlalchemy import create_engine, text  # noqa: PLC0415

        engine = create_engine(
            target.url,
            pool_pre_ping=True,
            pool_size=target.pool_size,
            max_overflow=target.max_overflow,
            future=True,
            connect_args={"connect_timeout": max(1, int(timeout))},
        )

        with engine.connect() as conn:
            select_one = conn.execute(text("SELECT 1")).scalar()
            version = conn.execute(text("SELECT VERSION()")).scalar()
            current_db = conn.execute(text("SELECT DATABASE()")).scalar()
            current_user = conn.execute(text("SELECT CURRENT_USER()")).scalar()
            table_count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = :database"
                ),
                {"database": target.database},
            ).scalar()

        print(f"      OK: SELECT 1 returned {select_one}")
        print(f"      server version : {version}")
        print(f"      current db     : {current_db}")
        print(f"      current user   : {current_user}")
        print(f"      tables in db   : {table_count}")
        print(f"      pool status    : {engine.pool.status()}")

        if pool_check_size:
            _run_pool_check(engine, pool_check_size)

        engine.dispose()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"      FAIL: {type(exc).__name__}: {exc}")
        _print_common_cause(str(exc), target)
        return False


def _run_pool_check(engine, size: int) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    print(f"      pool check     : opening {size} simultaneous connection(s)")
    connections = []
    try:
        for _ in range(size):
            conn = engine.connect()
            conn.execute(text("SELECT 1")).scalar()
            connections.append(conn)
        print("      pool check     : OK")
        print(f"      pool status    : {engine.pool.status()}")
    finally:
        for conn in connections:
            conn.close()


def _print_common_cause(message: str, target: Target) -> None:
    lowered = message.lower()
    if "1045" in message or "access denied" in lowered:
        print("      Cause: username/password was rejected by MySQL.")
    elif "1049" in message or "unknown database" in lowered:
        print(f"      Cause: database {target.database!r} does not exist.")
    elif "1130" in message or "not allowed to connect" in lowered:
        print("      Cause: this source host is not allowed by the MySQL user grants.")
    elif "2003" in message or "can't connect" in lowered:
        print("      Cause: MySQL is unreachable at the configured host/port.")
    elif "timeout" in lowered or "timed out" in lowered:
        print("      Cause: network path is blocked or the server did not respond in time.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("DB_CONNECT_TIMEOUT", "5")),
        help="TCP and driver connect timeout in seconds. Default: 5.",
    )
    parser.add_argument(
        "--pool-check",
        action="store_true",
        help="Open multiple simultaneous connections after the normal check.",
    )
    parser.add_argument(
        "--pool-check-size",
        type=int,
        default=None,
        help="Number of simultaneous connections for --pool-check.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_dotenvs()

    try:
        target = _resolve_target()
    except Exception as exc:  # noqa: BLE001
        print(f"Configuration error: {exc}")
        return 1

    pool_check_size = None
    if args.pool_check:
        pool_check_size = args.pool_check_size or min(
            target.pool_size + target.max_overflow,
            30,
        )
        if pool_check_size <= 0:
            print("--pool-check-size must be greater than zero.")
            return 1

    print("=" * 64)
    print("MySQL connection diagnostic")
    print("=" * 64)
    print(f"source         : {target.source}")
    print(f"host           : {target.host}")
    print(f"port           : {target.port}")
    print(f"user           : {target.user}")
    print(f"database       : {target.database}")
    print(f"pool_size      : {target.pool_size}")
    print(f"max_overflow   : {target.max_overflow}")
    print(f"url            : {_masked_url(target.url, target.password)}")
    print("-" * 64)

    if not _tcp_check(target, args.timeout):
        print("-" * 64)
        print("RESULT: FAILED")
        return 1

    if not _sql_check(target, args.timeout, pool_check_size):
        print("-" * 64)
        print("RESULT: FAILED")
        return 1

    print("-" * 64)
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
