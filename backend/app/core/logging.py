import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import settings

# Anchored to the backend root (/app in the container, ./backend on the host via
# the bind mount), so the log file is reachable from the host regardless of cwd.
_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOG_DIR = os.path.join(_BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "app.log")


def configure_logging() -> None:
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    formatter = logging.Formatter(
        fmt='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}'
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    # Rotating file log → ./backend/logs/app.log on the host (10 MB × 5 files).
    os.makedirs(LOG_DIR, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [stream_handler, file_handler]
    root.setLevel(level)

    # uvicorn keeps its own stdout handlers with propagate=False, so its request
    # access lines and startup/error logs never reach the root file handler.
    # Mirror them into the file too, so app.log is a complete record.
    # NB: "uvicorn.error" propagates to "uvicorn", so attaching to the parent is
    # enough — adding it to both would write every error line to the file twice.
    for name in ("uvicorn", "uvicorn.access"):
        logging.getLogger(name).addHandler(file_handler)
