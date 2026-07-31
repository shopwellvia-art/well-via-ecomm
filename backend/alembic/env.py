from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base  # imports all models

config = context.config
# Escape % for configparser interpolation (passwords may be URL-encoded).
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

if config.config_file_name is not None:
    # `disable_existing_loggers` defaults to True, which sets `.disabled = True`
    # on every logger that is not named in alembic.ini — including "httpx",
    # "httpcore" and the app's own loggers. That is harmless for the usual
    # `alembic` CLI invocation, where the process exits straight afterwards, but
    # this module is ALSO executed in-process: several tests generate their twin
    # SQL by driving alembic directly. There, one migration run permanently
    # silenced application logging for the rest of the pytest session.
    #
    # The visible symptom was two credential-leak tests failing in a full run
    # while passing alone. Both are written to refuse a vacuous pass — they
    # assert httpx actually logged a request line before asserting no secret
    # appears in it — so a disabled logger tripped their self-check rather than
    # letting them report a clean bill of health on zero evidence. Without that
    # guard this would have read as "no leak found" forever.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
