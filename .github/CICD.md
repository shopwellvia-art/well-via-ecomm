# CI/CD Guide — Well-Via E-commerce

Complete, beginner-friendly documentation of how Continuous Integration (CI)
and Continuous Deployment (CD) work in this repository, using **GitHub Actions**.

> **TL;DR**
> - Push code → GitHub automatically **checks** it (CI).
> - Push to the **`production`** branch → GitHub automatically **deploys** it to the EC2 server (CD).
> - All the recipes live in `.github/workflows/*.yml`.

---

## Table of contents

1. [What is CI/CD?](#1-what-is-cicd)
2. [The big picture (how a change reaches the live site)](#2-the-big-picture)
3. [The three workflows in this repo](#3-the-three-workflows-in-this-repo)
4. [Anatomy of a workflow file (YAML reference)](#4-anatomy-of-a-workflow-file)
5. [Secrets — the passwords the pipeline needs](#5-secrets)
6. [⚠️ The #1 gotcha: workflows run from their own branch](#6-the-1-gotcha)
7. [How to deploy to production (step by step)](#7-how-to-deploy-to-production)
8. [How to add CI/CD from scratch (if starting a new repo)](#8-how-to-add-cicd-from-scratch)
9. [Running the same checks locally](#9-running-the-same-checks-locally)
10. [Troubleshooting common failures](#10-troubleshooting)
11. [Glossary](#11-glossary)

---

## 1. What is CI/CD?

Think of it as a **robot assistant** that watches your repository.

| Term | Full name | Plain meaning |
|------|-----------|---------------|
| **CI** | Continuous **Integration** | Every time you push code, automatically **check it still works** (build it, run tests). Catches mistakes early. |
| **CD** | Continuous **Deployment** | When the checks pass on the release branch, automatically **ship the code to the server** so users get the new version — no manual SSH needed. |

**GitHub Actions** is the tool that runs these checks. It executes your recipe on
GitHub's own computers (called **runners**) — you don't need your own server for CI.

---

## 2. The big picture

Here is the journey of one code change, from your laptop to real users:

```
  You edit code
        │
        │  git push  (to your working branch, e.g. vinay)
        ▼
 ┌─────────────────────────────────────────────┐
 │  CI runs automatically:                      │
 │    • ci.yml   → frontend build + py compile  │
 │    • test.yml → backend integration tests    │
 └─────────────────────────────────────────────┘
        │
        │  Open a Pull Request → review → merge into `production`
        ▼
 ┌─────────────────────────────────────────────┐
 │  Push to `production` triggers CD (deploy.yml):│
 │    1. Build frontend + backend Docker images │
 │    2. Push images to GHCR (image registry)   │
 │    3. SSH into EC2 → pull images → restart    │
 │    4. Run DB migrations (alembic upgrade)     │
 └─────────────────────────────────────────────┘
        │
        ▼
   🌐 Live site updated
```

**Branch roles in this repo:**

| Branch | Role | What happens on push |
|--------|------|----------------------|
| `vinay` | a developer's working branch | CI runs (`ci.yml` + `test.yml`). No deploy. |
| `production` | the release branch | CI runs **and** CD deploys to EC2. |
| `main`, `kavya`, `Zorven` | other branches | `test.yml` runs (it runs on every push). |

---

## 3. The three workflows in this repo

All three live in `.github/workflows/`.

### 3.1 `ci.yml` — fast "does it still build?" check

- **When:** push to `vinay`, `main`, `production`, or **any** Pull Request.
- **What:** two jobs run in parallel on fresh Ubuntu machines:
  - **`frontend`** — install Node 20, `npm ci`, then `npm run build` (the real gate). Lint and unit tests are included but **non-blocking** for now (see notes in the file).
  - **`backend`** — `python -m compileall backend/app` — fails only if a Python file has a syntax error. It needs **no** database, so it finishes in seconds.
- **Deploys?** No. It never touches a server. Safe to run anywhere.

### 3.2 `test.yml` — heavy backend integration tests

- **When:** **every** push (no branch filter) + manual run.
- **What:** brings up a real stack with `docker compose` (MySQL + Redis + backend),
  applies Alembic migrations, then runs the **payment** integration test suite and
  the DTDC provider unit tests. Tears everything down at the end.
- **Why compose (not bare pytest)?** These are *integration* tests that talk to a
  live DB via `SessionLocal` and the FastAPI `TestClient`, so they need the real
  services running.
- **Deploys?** No.

### 3.3 `deploy.yml` — the CD pipeline (build + deploy)

- **When:** push to **`production`** + manual run.
- **What:** two jobs, run one after the other (`deploy` `needs: build-and-push`):
  1. **`build-and-push`** — builds the frontend and backend Docker images and
     pushes them to **GHCR** (GitHub Container Registry) tagged with both `latest`
     and the exact git commit SHA. Uses build cache to stay fast.
     - Images: `ghcr.io/<owner>/simple-com-backend` and `.../simple-com-frontend`
  2. **`deploy`** — copies `docker-compose.deploy.yml` to the EC2 host, then SSHes
     in and runs: `docker compose pull` → `up -d` → `alembic upgrade head` →
     `docker image prune -f`.
- **Deploys?** **Yes — to the live EC2 server.** This is the only workflow that
  changes production.

---

## 4. Anatomy of a workflow file

Every workflow is YAML with the same skeleton. Learn these keywords once and you
can read any workflow.

```yaml
name: CI                        # label shown in the Actions tab

on:                             # WHEN to run (the "trigger")
  push:
    branches: [production]      #   only pushes to these branches
  pull_request:                 #   and every PR
  workflow_dispatch:            #   plus a manual "Run workflow" button

concurrency:                    # cancel an older run if a newer push arrives
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:                           # the work; jobs run in PARALLEL by default
  frontend:                     # <- job id
    name: Frontend build        # <- pretty name
    runs-on: ubuntu-latest      # the machine type GitHub gives you
    needs: build                # (optional) wait for another job first
    defaults:
      run:
        working-directory: frontend   # run shell steps from this folder
    steps:                      # ordered instructions
      - uses: actions/checkout@v4     # "uses" = a reusable pre-made ACTION
      - uses: actions/setup-node@v4
        with:                   # "with" = settings passed to the action
          node-version: 20
      - name: Build             # "name" = label for this step
        run: npm run build      # "run" = a shell command you type
        env:                    # (optional) environment variables
          NODE_ENV: production
```

**Key words cheat-sheet:**

| Keyword | Meaning |
|---------|---------|
| `on` | The event(s) that trigger the workflow (`push`, `pull_request`, `workflow_dispatch`, `schedule`…). |
| `jobs` | Named groups of steps. Each job gets a **fresh** machine. |
| `runs-on` | Which OS/runner (`ubuntu-latest` is the usual choice). |
| `steps` | The ordered list of things a job does. |
| `uses` | Run a **reusable action** from the marketplace (e.g. `actions/checkout@v4`). |
| `with` | Inputs/settings for a `uses` action. |
| `run` | Run a raw shell command. |
| `env` | Environment variables for a step/job/workflow. |
| `needs` | Make one job wait for another (creates order). |
| `if` | Run a step/job only under a condition (e.g. `if: failure()`). |
| `${{ ... }}` | An **expression** — read a variable, secret, or context (e.g. `${{ secrets.EC2_HOST }}`, `${{ github.sha }}`). |
| `concurrency` | Prevent overlapping runs; cancel stale ones. |

---

## 5. Secrets

**Never put passwords, SSH keys, or tokens in a workflow file.** They go in
**GitHub → repo → Settings → Secrets and variables → Actions → "New repository secret"**,
and the workflow reads them with `${{ secrets.NAME }}`.

### Secrets used by `deploy.yml`

| Secret | What it is | Example / where to get it |
|--------|-----------|---------------------------|
| `EC2_HOST` | Public IP or DNS of the EC2 server | `13.204.xxx.xxx` |
| `EC2_USER` | SSH username on that server | `ubuntu` or `ec2-user` |
| `EC2_APP_DIR` | Folder on EC2 that holds the compose file | `/home/ubuntu/well-via` |
| `SSH_PRIVATE_KEY` | Private key that can SSH into EC2 | contents of your `.pem` / `id_ed25519` |
| `GHCR_PAT` | GitHub token used **on the EC2** to pull private images | a Personal Access Token with `read:packages` |

> `GITHUB_TOKEN` (used to **push** images to GHCR inside the Actions runner) is
> provided **automatically** by GitHub — you do **not** create it. It only needs
> `packages: write` permission, which the job already declares.

`ci.yml` and `test.yml` need **no secrets** — that's why they're a safe place to start.

### How to add a secret (click-by-click)

1. Open the repo on GitHub.
2. **Settings** (top bar) → left sidebar **Secrets and variables** → **Actions**.
3. Click **New repository secret**.
4. **Name** = e.g. `EC2_HOST`, **Secret** = the value. **Add secret**.
5. Repeat for each secret above. You can never *read* a secret back — only overwrite it.

---

## 6. The #1 gotcha

> **For a `push` event, GitHub runs the workflow file exactly as it exists on the
> branch you pushed to — not from any other branch.**

Consequences:

- Editing `deploy.yml` on `vinay` does **nothing** for production until that file
  is **merged into `production`**.
- The moment the updated `deploy.yml` (with `branches: [production]`) lands on
  `production`, the **next push to `production` deploys to the live server**.
- If you change a trigger, always ask: *"Is this file on the branch that will
  actually fire it?"*

For **`pull_request`**, GitHub uses the workflow from the PR's **base** branch —
another reason to keep workflow files consistent across branches.

---

## 7. How to deploy to production

**Prerequisites (do these once):**

1. All five `deploy.yml` secrets from [section 5](#5-secrets) are set.
2. On the EC2 host, `EC2_APP_DIR` exists and Docker + Docker Compose are installed.
3. The updated workflow files exist **on the `production` branch** (the gotcha).

**To release (every time):**

```bash
# 1. Make sure your changes are on your working branch and CI is green.
git checkout vinay
git push origin vinay            # CI runs; confirm green tick in Actions tab

# 2. Move the reviewed changes onto production.
git checkout production
git merge --ff-only vinay        # or open & merge a Pull Request on GitHub
git push origin production       # ← THIS push triggers deploy.yml (goes live)

# 3. Back to work.
git checkout vinay
```

Then open **Actions → "Build & Deploy to EC2"** and watch the two jobs. If the
`deploy` job is green, the live site is updated. If it's red, see
[Troubleshooting](#10-troubleshooting).

> You can also deploy the current `production` code **without a new commit** via
> **Actions → Build & Deploy to EC2 → Run workflow** (that's what `workflow_dispatch` enables).

---

## 8. How to add CI/CD from scratch

If you ever start a fresh repo, this is the whole process:

1. **Create the folder:** `.github/workflows/` at the repo root.
2. **Add a CI file**, e.g. `ci.yml`, with:
   - an `on:` trigger (`push` + `pull_request`),
   - a job that checks out code, sets up the language, installs deps, and builds/tests.
3. **Commit and push.** GitHub auto-detects the file — no "enable" button needed.
4. **Watch it** under the **Actions** tab.
5. **Add a deploy file** (`deploy.yml`) only when you have a server to deploy to,
   and store server credentials as **Secrets**.
6. **Protect the release branch** (optional but recommended):
   Settings → Branches → add a rule on `production` requiring CI to pass before merge.

A minimal starter CI (any Node project):

```yaml
name: CI
on:
  push:
  pull_request:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: npm, cache-dependency-path: frontend/package-lock.json }
      - run: npm ci
        working-directory: frontend
      - run: npm run build
        working-directory: frontend
```

---

## 9. Running the same checks locally

Run the exact commands the pipeline runs, **before** you push — it's faster than
waiting for the runner.

**Frontend (mirrors `ci.yml`):**
```bash
cd frontend
npm ci
npm run build          # the real gate
npm run lint           # optional (needs an ESLint v9 config to pass)
npm test -- --run --passWithNoTests
```

**Backend syntax check (mirrors `ci.yml`):**
```bash
python -m compileall backend/app
```

**Backend integration tests (mirrors `test.yml`):**
```bash
# from repo root — needs Docker running
docker compose up -d --build mysql redis backend
docker compose exec -T backend alembic upgrade head
docker compose exec -T -u root backend pip install --no-cache-dir -r requirements-dev.txt
docker compose exec -T backend python -m pytest -v
docker compose down -v   # clean up
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| CI never starts after a push | Branch not in `ci.yml`'s `on.push.branches`, or the file isn't on that branch | Add the branch to `ci.yml`; ensure the file is committed on that branch. |
| Frontend job red at "Build the app" | Real build error (bad import, syntax) | Reproduce with `npm run build` locally; fix the error. |
| Lint step red | ESLint v9 needs a flat `eslint.config.js` | It's currently `continue-on-error` (non-blocking). Add the config to make it a real gate. |
| `test.yml` red at "Wait for MySQL" | DB container didn't become healthy | Check `docker compose logs mysql`; the workflow dumps logs on failure. |
| Deploy red at "Copy compose file"/SSH step | Missing/wrong secret (`EC2_HOST`, `SSH_PRIVATE_KEY`, …) | Re-check the 5 secrets in section 5; confirm the key can SSH manually. |
| Deploy red at "Pull images" | EC2 can't authenticate to GHCR | `GHCR_PAT` invalid or lacks `read:packages`; regenerate it. |
| Pushed to `production` but nothing deployed | `deploy.yml` on `production` still triggers on `main` (the gotcha) | Merge the updated `deploy.yml` into `production`. |
| Deploy succeeds but site unchanged | Browser/CDN cache, or migrations pending | Hard refresh; check `alembic upgrade head` output in the deploy log. |

**Where to read logs:** repo → **Actions** tab → click the run → click a job →
expand any step to see its full output. Failed steps are marked red ❌.

---

## 11. Glossary

- **Workflow** — one recipe file (`.yml`) in `.github/workflows/`.
- **Job** — a group of steps that runs on one fresh machine. Jobs are parallel unless linked by `needs`.
- **Step** — a single instruction: either a reusable `uses:` action or a `run:` shell command.
- **Action** — a packaged, reusable step (e.g. `actions/checkout`, `docker/build-push-action`).
- **Runner** — the machine (here `ubuntu-latest`) that GitHub gives you to run a job.
- **Trigger** — the event in `on:` that starts a workflow (`push`, `pull_request`, `workflow_dispatch`, `schedule`).
- **Secret** — an encrypted value (password/key/token) stored in repo settings, read via `${{ secrets.NAME }}`.
- **GHCR** — GitHub Container Registry (`ghcr.io`), where the Docker images are stored.
- **Artifact/Image** — the built output; here, Docker images tagged with `latest` and the git SHA.
- **SHA** — the unique id of a git commit; used as an image tag so every deploy is traceable.
- **CI / CD** — Continuous Integration (auto-check) / Continuous Deployment (auto-ship).

---

*Files referenced:* `.github/workflows/ci.yml`, `.github/workflows/test.yml`,
`.github/workflows/deploy.yml`, `docker-compose.deploy.yml`.
