from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # Recycle connections well before a cloud MySQL / firewall idle-timeout can
    # silently drop them. Without this, idle pooled connections get reaped
    # server-side and the next query fails with "(2013) Lost connection to MySQL
    # server during query" — or hangs while pre_ping probes a dead socket.
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    # Bound socket ops so a half-dead connection fails fast instead of hanging
    # the request (and, transitively, health checks) for the OS TCP timeout.
    connect_args={
        "connect_timeout": settings.DB_CONNECT_TIMEOUT,
        "read_timeout": settings.DB_READ_TIMEOUT,
        "write_timeout": settings.DB_READ_TIMEOUT,
        # Pin every connection to UTC. `TimestampMixin` fills created_at /
        # updated_at with server_default=func.now() and onupdate=func.now(),
        # which MySQL evaluates in the SERVER's time_zone — while all Python
        # writes use datetime.now(timezone.utc). Without this pin, whether a
        # row's timestamp is UTC depends on which of the two wrote it and on a
        # shared remote MySQL's `time_zone` setting we do not control, so the
        # naive-UTC invariant that analytics day-bucketing relies on (see
        # services/analytics/timebox.py) holds only by luck.
        #
        # Set on the session, not the server: it needs no privileges and cannot
        # affect the other applications sharing that MySQL host.
        "init_command": "SET time_zone = '+00:00'",
    },
    future=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
    future=True,
)
