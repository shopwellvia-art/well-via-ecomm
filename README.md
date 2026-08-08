# Ecommerce Platform

Production-ready ecommerce starter: FastAPI + React + MySQL + Redis, orchestrated with Docker.

## Stack

- **Backend:** FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, PASETO v4.local auth
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

**Backend** tests run from your local venv against the throwaway MySQL/Redis
containers above — never inside Docker: `tests/` and pytest are deliberately
excluded from the production image (see `backend/.dockerignore` and
`requirements-dev.txt`), so there is nothing to `docker compose exec` into.

```bash
cd backend
source .venv/bin/activate
pip install -r requirements-dev.txt   # requirements.txt + test tooling
alembic upgrade head                  # against the throwaway DB ONLY
python -m pytest tests/ -v
```

`backend/tests/conftest.py` aborts the whole session unless `MYSQL_HOST` is a
local host and `ENVIRONMENT` is `test`/`development`/`ci` — so a mispointed
`.env` fails safe instead of touching the shared remote DB.

**Frontend** checks are plain npm scripts:

```bash
cd frontend
npm run lint          # ESLint
npm test              # Vitest (watch mode; CI uses `npx vitest run`)
npm run test:e2e      # Playwright end-to-end — needs the app running
npm run build
```

CI (`.github/workflows/cicd.yml`) runs the same suites — frontend lint + Vitest
+ build, and the backend pytest suite against throwaway MySQL/Redis service
containers — on every push to `production`, and blocks the deploy if any fail.
E2E is local-only (it needs a live stack).

## Production

Deployment is automated — push to the `production` branch. See **DEPLOY.md**.

## Architecture

- `api/` — HTTP only; converts requests/responses, no business logic
- `services/` — business rules, transactions; owns the use case
- `repositories/` — data access; the only layer that touches the ORM
- `models/` — SQLAlchemy ORM
- `schemas/` — Pydantic DTOs

Dependency direction is one-way: `api → service → repository → model`.
