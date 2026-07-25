# CI/CD Guide — Well-Via E-commerce

Complete, beginner-friendly documentation of how Continuous Integration (CI)
and Continuous Deployment (CD) work in this repository, using **GitHub Actions**.

> **TL;DR**
> - Push to the **`production`** branch → GitHub builds the frontend, builds both
>   Docker images **from scratch**, and **deploys** them to the EC2 server (CD).
> - There is exactly **one** recipe: `.github/workflows/cicd.yml`.
> - There is exactly **one** compose file: `docker-compose.yml` (production).
> - ⚠️ **No test suites run in CI.** Vitest and the pytest/MySQL integration job
>   were removed on purpose. `npm run build` is the only automated gate. **You are
>   the test gate now** — run pytest locally before you merge ([§9](#9-running-the-same-checks-locally)).

---

## Table of contents

1. [What is CI/CD?](#1-what-is-cicd)
2. [The big picture (how a change reaches the live site)](#2-the-big-picture)
3. [The one workflow in this repo](#3-the-one-workflow-in-this-repo)
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
| **CI** | Continuous **Integration** | Every time you push code, automatically **check it still works**. In *this* repo that check is a **build only** — no test suites (see the TL;DR). |
| **CD** | Continuous **Deployment** | When the checks pass on the release branch, automatically **ship the code to the server** so users get the new version — no manual SSH needed. |

**GitHub Actions** is the tool that runs these checks. It executes your recipe on
GitHub's own computers (called **runners**) — you don't need your own server for CI.

---

## 2. The big picture

Here is the journey of one code change, from your laptop to real users:

```
  You edit code on a working branch (e.g. vinay)
        │
        │  Nothing automatic happens here — the pipeline is
        │  production-only. Run the tests yourself (§9); CI
        │  will NOT catch a broken test for you.
        │
        │  git merge / PR-merge into `production`, then push
        ▼
 ┌───────────────────────────────────────────────────┐
 │  A push to `production` runs cicd.yml end to end.  │
 │  Quality gate (build only, no tests):              │
 │    • frontend → npm ci, lint, npm run build        │
 └───────────────────────────────────────────────────┘
        │
        │  ONLY if it is green:
        ▼
 ┌───────────────────────────────────────────────────┐
 │    1. Build BOTH images from scratch (--no-cache)  │
 │    2. Push images to GHCR (image registry)         │
 │    3. SSH into EC2 → pull → force-recreate the     │
 │       backend+frontend containers, then DELETE     │
 │       the superseded images (redis untouched)      │
 │    4. Verify both run the new SHA + site answers   │
 │       (NO DB migrations — see DEPLOY.md §6)        │
 └───────────────────────────────────────────────────┘
        │
        ▼
   🌐 Live site updated
```

**Branch roles in this repo:**

| Branch | Role | What happens on push |
|--------|------|----------------------|
| `production` | the release branch | The **whole** pipeline runs: frontend build → image build → deploy to EC2. |
| `vinay`, `main`, anything else | working branches | **Nothing.** No CI, no deploy. |

> The pipeline is deliberately **`production`-only** (`on.push.branches:
> [production]`, no `pull_request` trigger). A merge into `production` *is* a
> push, so merging is all it takes to ship. Before you merge, run the same
> checks locally — see [section 9](#9-running-the-same-checks-locally).

---

## 3. The one workflow in this repo

Everything lives in a single file: **`.github/workflows/cicd.yml`**. It has three
jobs, all of which only ever run for `production`. The first is the quality gate;
the last two are the delivery half and run **only if the first one passes**.

### 3.1 `frontend` — build the SPA

- **When:** push/merge to `production`, or a manual run on `production`.
- **What:** Node 20 → `npm ci` → `npm run lint` → `npm run build`.
- The **build** is the real gate. Lint is **non-blocking** because ESLint v9 needs
  a flat `eslint.config.js` this repo does not have yet; remove the
  `continue-on-error` once that lands.
- **No Vitest.** The unit-test step was removed. This job exists purely as a cheap
  *pre-flight*: it fails a broken frontend in ~2 min instead of ~8 min into the
  no-cache Docker build. Strictly speaking it is redundant — the frontend
  Dockerfile runs `npm run build` too — it just fails faster and cheaper.
- **Deploys?** No.

### 3.2 `build-and-push` — build images from scratch, push to GHCR

- **When:** `needs: [frontend]` **and** `github.ref == 'refs/heads/production'`.
  (The workflow only triggers on `production` anyway; the `if:` also blocks a
  manual run launched from some other branch in the Actions UI.)
- **What:** builds both Docker images and pushes them to **GHCR** tagged with both
  `latest` and the exact git commit SHA.
  - Images: `ghcr.io/<owner>/simple-com-backend` and `.../simple-com-frontend`
- **Every build is a full rebuild**, by design:

  | Flag | Effect |
  |------|--------|
  | `no-cache: true` | Every Dockerfile layer re-executes. `pip install` and `npm ci` genuinely re-resolve instead of restoring a cached layer. |
  | `pull: true` | Re-pulls the `FROM` base image, so a patched python/node/nginx base is actually picked up. |
  | *(no `cache-from`/`cache-to`)* | The GitHub Actions layer cache was removed — there is nothing to fall back on. |
  | `provenance: false` | Pushes a plain image manifest instead of an attestation index, which keeps `docker pull` on the host simple. |

  **Cost:** ~6–10 min per run instead of ~2. If that ever becomes too slow, delete
  `no-cache`/`pull` and restore `cache-from: type=gha` + `cache-to: type=gha,mode=max`.
- This job is also the **de-facto backend gate**: there is no pytest job any more,
  so a broken `requirements.txt` or a Python syntax error surfaces here, when the
  image fails to build.
- **Deploys?** Not yet — it only publishes images.

### 3.3 `deploy` — recreate the containers and delete the old images

- **When:** `needs: build-and-push`.
- **What:** copies `docker-compose.yml` to the EC2 host, then SSHes in and:
  1. `docker login ghcr.io`
  2. writes `IMAGE_TAG=<sha>` into the compose project's root `.env`, so the tag
     is pinned for later manual `docker compose` calls on the host too
  3. **records the image IDs of the currently running** `backend`/`frontend`
     containers — captured *before* the pull, while the old `:latest` still resolves
  4. `docker compose pull backend frontend` — scoped to those two services **on
     purpose**: `redis` must not be re-pulled or restarted, because carts,
     refresh-token sessions and OTPs live in it
  5. `docker compose up -d --force-recreate --remove-orphans backend frontend` —
     **this is the restart.** `--force-recreate` means the containers are destroyed
     and rebuilt from the new images *every* deploy; it no longer relies on the
     image reference having changed. `redis` is not named, so it keeps running and
     its volume is never touched.
  6. `docker rmi -f` on the image IDs recorded in step 3 — **this is the "delete the
     old images" step.** It runs only *after* the new containers are up, and Docker
     refuses to remove an image backing a running container, so it can never pull
     the live stack out from under itself.
  7. `docker image prune -af` + `docker builder prune -af` — sweeps dangling layers
     and older `:<sha>` tags from previous deploys, then prints `docker images`.
- Then a separate **verify** step re-SSHes and asserts the deploy really landed:
  every app container must report `:<sha>` as its image, and
  `curl http://localhost:8090/` must answer (retried for a minute). If either
  check fails the job goes **red** and dumps the last 50 log lines — so a
  silently-skipped recreate can't masquerade as a successful deploy.
- **No `alembic upgrade head`.** The shared remote MySQL is on a migration lineage
  this repo does not contain; schema changes are applied manually from reviewed
  SQL. See `DEPLOY.md` §6.
- **Deploys?** **Yes — to the live EC2 server.** This is the only job that changes
  production.

> ⚠️ **Two consequences of the aggressive cleanup, worth knowing:**
>
> 1. **`docker image prune -af` is host-wide, not project-scoped.** It removes *any*
>    image on that EC2 with no container attached — including one another project
>    pulled but never ran. Images backing a running *or stopped* container are safe,
>    as are `redis:7-alpine` and the two new app images.
> 2. **Rollback now costs a re-pull.** Old `:<sha>` images used to linger on the host,
>    so `IMAGE_TAG=<old-sha> docker compose up -d` was instant. They are deleted now.
>    Rollback still works — the images are still in GHCR — it just has to download
>    again first.

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

### Secrets used by the `deploy` job

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

The `frontend` job needs **no secrets** — that's why it's a safe place to start.

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

- Editing `cicd.yml` on `vinay` does **nothing** for production until that file
  is **merged into `production`**.
- The moment the updated `cicd.yml` lands on `production`, the **next push to
  `production` deploys to the live server** (assuming CI is green).
- If you change a trigger, always ask: *"Is this file on the branch that will
  actually fire it?"*

Because this pipeline is `production`-only, the gotcha is narrower than usual:
the only copy of `cicd.yml` that ever runs is the one **on `production`**. Edits
made anywhere else are inert until they are merged in.

---

## 7. How to deploy to production

**Prerequisites (do these once):**

1. All five deploy secrets from [section 5](#5-secrets) are set.
2. On the EC2 host, `EC2_APP_DIR` exists, holds a filled-in `backend/.env`, and
   Docker + Docker Compose are installed.
3. The updated `cicd.yml` exists **on the `production` branch** (the gotcha).

**To release (every time):**

```bash
# 1. Run the checks locally first (see §9). This is MANDATORY now: the pipeline
#    runs no tests at all, so a local pytest run is the ONLY thing standing
#    between a broken backend and production.
git checkout vinay
git push origin vinay            # no CI fires; this is just backup/sharing

# 2. Merge the reviewed changes into production and push.
git checkout production
git merge --ff-only vinay        # or open & merge a Pull Request on GitHub
git push origin production       # ← THIS is the deploy trigger

# 3. Back to work.
git checkout vinay
```

A **merge is just a push**, so merging a PR into `production` on GitHub triggers
the exact same pipeline — you don't have to push from the terminal.

Then open **Actions → "CI/CD"** and watch the three jobs. `build-and-push` and
`deploy` only start once `frontend` is green. Expect the run to take **~10–15
minutes** — the images are rebuilt with `--no-cache` every time. If the `deploy`
job is green, the live site is updated. If it's red, see
[Troubleshooting](#10-troubleshooting).

> You can also redeploy the current `production` code **without a new commit** via
> **Actions → CI/CD → Run workflow** (that's what `workflow_dispatch` enables) —
> pick the `production` branch. Because builds are `--no-cache`, a manual re-run
> genuinely rebuilds both images from scratch (fresh `npm ci` / `pip install` and a
> re-pulled base image) rather than reusing anything from the previous run.

---

## 8. How to add CI/CD from scratch

If you ever start a fresh repo, this is the whole process:

1. **Create the folder:** `.github/workflows/` at the repo root.
2. **Add one workflow file**, e.g. `cicd.yml`, with:
   - an `on:` trigger (`push` + `pull_request`),
   - a job that checks out code, sets up the language, installs deps, and builds/tests.
3. **Commit and push.** GitHub auto-detects the file — no "enable" button needed.
4. **Watch it** under the **Actions** tab.
5. **Add the deploy jobs to the same file** once you have a server to deploy to.
   Gate them with `needs:` (so they wait for the checks) and an `if:` on the
   release branch, and store server credentials as **Secrets**.
6. **Protect the release branch** (optional but recommended):
   Settings → Branches → add a rule on `production` requiring CI to pass before merge.

> **Why one file rather than several?** Splitting CI and CD across workflows means
> the deploy has no direct way to depend on the tests — you end up reaching for
> `workflow_run`, which is easy to get subtly wrong (this repo's old `deploy.yml`
> claimed to be test-gated but wasn't). Jobs in a *single* workflow can just say
> `needs:`, which is unambiguous and visible in one graph.

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

> 🚨 **This section is no longer optional.** The pipeline runs **no tests**, so
> these local commands are the *only* automated verification this project gets.
> Treat a local `pytest` run as a required step before merging to `production`.

**Frontend build (mirrors the `frontend` job):**
```bash
cd frontend
npm ci
npm run build          # the real gate — this IS what CI runs
npm run lint           # optional (needs an ESLint v9 config to pass)
npm test -- --run      # NOT run by CI any more — run it yourself
```

**Backend integration tests — no longer in CI, run these yourself:**

The `backend-tests` job used to do exactly this on every push. It was removed, so
you must reproduce it locally. The commands below are what that job ran, verbatim.

```bash
# 1. Throwaway services, same images/credentials the CI job uses.
docker run -d --name ci-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root -e MYSQL_DATABASE=ecommerce \
  -e MYSQL_USER=ecom -e MYSQL_PASSWORD=ecom_password mysql:8
docker run -d --name ci-redis -p 6379:6379 redis:7-alpine

# 2. Point backend/.env at them — NEVER at the shared remote MySQL.
#    (conftest.py aborts the run if you do.) Use MYSQL_HOST=127.0.0.1,
#    REDIS_URL=redis://127.0.0.1:6379/0, ENVIRONMENT=ci.

# 3. Run it.
cd backend
pip install -r requirements-dev.txt
alembic upgrade head
python -m pytest tests/ -v

# 4. Clean up.
docker rm -f ci-mysql ci-redis
```

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Nothing runs after a push | You pushed to a branch other than `production` | Expected — the pipeline is `production`-only by design. Run the checks locally (§9), then merge. |
| Nothing runs after a push **to `production`** | The `cicd.yml` on `production` is an older copy without the `production` trigger | Merge the current `cicd.yml` into `production`. |
| Frontend job red at "Build" | Real build error (bad import, syntax) | Reproduce with `npm run build` locally; fix the error. |
| Lint step red | ESLint v9 needs a flat `eslint.config.js` | It's currently `continue-on-error` (non-blocking). Add the config to make it a real gate. |
| `build-and-push` red in the **backend** build | Broken `requirements.txt`, a pinned version that no longer resolves, or a Python error at image build time | With no pytest job, this is now where backend breakage first appears. Reproduce with `docker build --no-cache ./backend`. |
| `build-and-push` red in the **frontend** build | Build error the `frontend` job somehow passed (different Node version, or a Docker-only step) | Reproduce with `docker build --no-cache ./frontend`. |
| Runs got much slower (~10–15 min) | **Expected.** `no-cache: true` + `pull: true` mean nothing is reused between runs | Working as designed. To trade freshness for speed, restore `cache-from`/`cache-to: type=gha` and drop `no-cache`/`pull` in `build-and-push`. |
| A broken test reached production | **Expected — CI runs no tests.** | Nothing in the pipeline will catch this. Run §9 locally before merging. |
| `build-and-push`/`deploy` skipped | Not on `production`, or the `frontend` job failed | Gated on `needs: [frontend]` **and** `github.ref == 'refs/heads/production'`. Fix the red job or push to `production`. |
| Deploy red at "Copy compose file"/SSH step | Missing/wrong secret (`EC2_HOST`, `SSH_PRIVATE_KEY`, …) | Re-check the 5 secrets in section 5; confirm the key can SSH manually. |
| Deploy red at the pull/recreate step | EC2 can't authenticate to GHCR | `GHCR_PAT` invalid or lacks `read:packages`; regenerate it. |
| Deploy log says `keeping <id> (still in use)` | **Not an error.** The old image is still backing a running container, so Docker declined to delete it | Normal, e.g. when redeploying the same SHA. The following `docker image prune -af` cleans up whatever is genuinely unreferenced. |
| An unrelated image vanished from the EC2 | `docker image prune -af` is **host-wide** — it removes any image with no container attached | Re-pull it. If that host holds images worth keeping, scope the cleanup to the two GHCR repos by tag instead of a blanket prune. |
| Rollback is slow now | Old `:<sha>` images are deleted from the host after each deploy | Expected. The images are still in GHCR: `IMAGE_TAG=<old-sha> docker compose pull backend frontend && docker compose up -d` — it just has to download first. |
| Carts/logins were lost after a deploy | Should **not** happen — the deploy never touches `redis` or its volume | Check whether someone ran `docker compose down` or pruned volumes manually. The pipeline only names `backend frontend` in its `pull`/`up` commands. |
| Pushed to `production` but nothing deployed | The `cicd.yml` on `production` is an older copy (the gotcha) | Merge the updated `cicd.yml` into `production`. |
| Deploy red at "Verify the new code is live" | Containers didn't come back on the new SHA, or the site didn't answer on `:8090` | The step prints each container's actual image and dumps 50 log lines. Usually a crash-looping backend (bad `backend/.env`) or an unreachable DB. |
| Deploy succeeds but site unchanged | Browser cache — the verify step already proved the new SHA is running | Hard refresh (Ctrl/Cmd-Shift-R). Note the pipeline runs **no** migrations — pending schema changes are applied manually (`DEPLOY.md` §6). |

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

*Files referenced:* `.github/workflows/cicd.yml` (the only workflow),
`docker-compose.yml` (the only compose file, production). For host setup,
secrets, rollback and migrations, see `DEPLOY.md`.
