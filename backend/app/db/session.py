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
