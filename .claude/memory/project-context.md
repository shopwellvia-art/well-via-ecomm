# Project Context — Simple Ecommerce

Long-lived facts about the project. Read this first; it is the orchestrator's
shared memory across sessions.

## Overview

A production-shape ecommerce platform: product catalog, cart, orders, and
PASETO-based auth. Built as a monorepo with a separated backend and frontend.

## Product Vision

A premium modern ecommerce platform with:
- immersive UI
- lightweight 3D interactions
- modern shopping UX
- fast checkout flow
- scalable architecture

Inspired by:
- Apple
- Stripe
- Framer
- Shopify

This changes how ALL agents think.

## Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, pyseto (PASETO v4.local), passlib |
| Frontend | React 18, Vite, React Router, TanStack Query, Zustand, axios |
| Database | MySQL 8.0 |
| Cache / queue | Redis 7, Celery |
| Infra | Docker, docker-compose, nginx reverse proxy |

## Repository layout

```
backend/    FastAPI service — clean architecture (api/service/repository/model)
frontend/   React SPA — feature-sliced
docker/     Shared nginx + mysql assets
scripts/    Helper scripts (check_db.py)
.claude/    Orchestrator, agents, skills, memory, tasks, prompts, docs
```

## Architecture in one line

Backend: `api → service → repository → model` (one-way).
Frontend: feature-sliced — each feature owns its api, hooks, store, components.
Full detail in `../docs/architecture-rules.md`.

## Environment

- Config is env-driven. Backend reads it via Pydantic `Settings`
  (`backend/app/core/config.py`).
- Secrets live in `backend/.env`, `frontend/.env`, root `.env` — all gitignored.
  Only `.env.example` files are tracked.
- Target database: remote **MySQL 8.0 at `<DB_HOST>`**, database
  **`ecommercesimple`**, user `vinay`.
  *(Live host IP was redacted — it is a leaked secret that must be rotated and purged from git history.)*
- The DB password contains special characters, so it is URL-encoded into the
  SQLAlchemy DSN; `alembic/env.py` escapes `%` for configparser.

## Current state

- Backend scaffolded: core, models, schemas, repositories, services, API v1.
- Database migrated — initial migration `5bfb07ce98a5_init` applied. Tables:
  `users`, `categories`, `products`, `orders`, `order_items`.
- Verified end-to-end: health, docs, register, login, `/me`, products list.
- Frontend scaffolded (pages, features, apiClient) — not yet browser-tested.
- Docker images build and run.

## Known issues / decisions

- `bcrypt` is pinned to `4.0.1` — bcrypt 5.x breaks passlib 1.7.4's self-test.
- The default catalog is empty: no products seeded, no admin user yet, so
  `POST /products` (admin-only) cannot be exercised until an admin exists.
- Redis is not required for auth; cart endpoints need it running.

## Not done yet

- Seed data and an admin user.
- Frontend browser verification.
- Real test suite (only a placeholder `test_health.py` exists).
- CI/CD pipeline.
- Payment integration (Stripe stubs only).

## Conventions snapshot

- API surface is versioned: `/api/v1/...`.
- Errors return `{"error": {"code", "message", "details"}}`.
- Schema changes always ship an Alembic migration — never `create_all`.
