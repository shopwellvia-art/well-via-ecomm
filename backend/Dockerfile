FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential default-libmysqlclient-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user -r requirements.txt


FROM python:3.12-slim AS runtime

# Build provenance — passed by CI (.github/workflows/cicd.yml) and by the
# emergency local build. Baked in as OCI labels (visible via `docker inspect`)
# AND as env the app reads to serve GET /version, so the exact commit running on
# the host is always verifiable. Defaults keep a plain `docker build` working.
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/home/app/.local/bin:$PATH \
    GIT_SHA=$GIT_SHA \
    BUILD_TIME=$BUILD_TIME

LABEL org.opencontainers.image.title="simple-com-backend" \
      org.opencontainers.image.source="https://github.com/shopwellvia-art/well-via-ecomm" \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.created=$BUILD_TIME

RUN apt-get update && apt-get install -y --no-install-recommends \
        default-libmysqlclient-dev curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 app

WORKDIR /app

COPY --from=builder /root/.local /home/app/.local
COPY --chown=app:app . .

# Writable uploads dir for the local storage backend (the app runs non-root,
# and /app itself is root-owned).
RUN install -d -o app -g app /app/uploads

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
