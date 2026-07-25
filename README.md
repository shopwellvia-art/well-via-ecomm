# Ecommerce Platform

Production-ready ecommerce starter: FastAPI + React + MySQL + Redis, orchestrated with Docker.

## Stack

- **Backend:** FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, JWT auth
- **Frontend:** React 18, Vite, React Query, Zustand, React Router
- **Database:** MySQL 8.4
- **Cache / queue:** Redis 7
- **Reverse proxy:** Nginx

## Layout

```
.
├── backend/            FastAPI service (clean architecture: api → service → repo → model)
├── frontend/           React SPA (feature-sliced); its image bundles nginx
├── loadtest/           Isolated k6 load-test stack (own compose file)
├── docker-compose.yml  PRODUCTION stack — the only compose file
└── .github/workflows/cicd.yml   The only CI/CD pipeline
```

`docker-compose.yml` is **production only** (it pulls prebuilt GHCR images and
talks to the external MySQL). Do not run it locally — see below.

## Local development

Run the two services natively; there is no local Docker stack.

```bash
# --- backend (needs a MySQL + Redis you can reach) ---
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # point MYSQL_* at a LOCAL throwaway DB
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# --- frontend ---
cd frontend
npm ci
npm run dev                   # http://localhost:5173, proxies /api → :8000
```

Need throwaway MySQL/Redis containers? Run them directly:

```bash
docker run -d --name dev-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=ecommerce \
  -e MYSQL_USER=ecom -e MYSQL_PASSWORD=ecom_password mysql:8
docker run -d --name dev-redis -p 6379:6379 redis:7-alpine
```

> Never point local work or tests at the shared remote MySQL.
> `backend/tests/conftest.py` aborts the run if you do.

## Tests

```bash
cd backend && python -m pytest tests/ -v     # needs the throwaway DB above
cd frontend && npm test -- --run
```

## Production

Deployment is automated — push to the `production` branch. See **DEPLOY.md**.

## Architecture

- `api/` — HTTP only; converts requests/responses, no business logic
- `services/` — business rules, transactions; owns the use case
- `repositories/` — data access; the only layer that touches the ORM
- `models/` — SQLAlchemy ORM
- `schemas/` — Pydantic DTOs

Dependency direction is one-way: `api → service → repository → model`.

## Testing

```bash
# backend
docker compose exec backend pytest

# frontend
docker compose exec frontend npm test
```
