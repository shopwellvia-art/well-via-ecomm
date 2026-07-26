# Deployment — CI/CD to AWS EC2 (GitHub Actions + GHCR)

There is exactly **one workflow** (`.github/workflows/cicd.yml`) and exactly
**one compose file** (`docker-compose.yml`, the production stack).

The pipeline runs for the **`production` branch only** — no other branch, no Pull
Requests. Push or merge into `production` and the whole thing runs end to end:
the frontend build and the backend test suite go first, and **only if both pass**
does it build the backend & frontend Docker images, push them to **GHCR**, then
SSH into your **EC2** to pull the new images and **restart both containers on the
new code**. The app is served over **plain HTTP on port 8090** of the EC2's public
IP (see [TLS termination](#tls-termination-required-before-go-live)).

Database schema changes are **NOT** applied by the pipeline — they are done
manually from reviewed SQL (see [§6](#6-migrations)).

```
push / merge → `production`   (nothing runs on any other branch)
   ├─ frontend         ─┐
   │  npm ci/build/test │
   ├─ backend-tests    ─┤ BOTH must pass
   │  mysql+redis svc   │
   │  alembic+pytest    │
   │                    ▼
   ├─── build-and-push → ghcr.io  (:latest and :<git-sha>)
   │                    │
   └─── deploy → ssh EC2 → compose pull → up -d  (recreates backend+frontend)
                         → verify both run :<git-sha> and :8090 answers
                         (no alembic — see §6)

EC2 host:  [frontend+nginx :8090→:80] ─proxy─> [backend :8000] ─> shared MySQL
                                                 [redis]           (external)
                                       [payment-reconcile-cron] ──┘ (every ~10m)
```

**Why the containers pick up the new code:** every deploy tags the images with
the commit SHA and pins `IMAGE_TAG=<sha>` on the host, so the running containers'
image references no longer match after a pull — `docker compose up -d` therefore
recreates the `backend` and `frontend` containers rather than leaving them alone.
`redis` keeps the same image, so it stays up and its volume is untouched. A final
verify step asserts both containers really report the new SHA and that the
storefront answers on `:8090`, and fails the deploy if not.

The backend suite runs against a **throwaway MySQL 8 + Redis 7** provided as
GitHub Actions `services:` containers — never against the shared remote DB
(`backend/tests/conftest.py` hard-fails the run if it is pointed anywhere but a
local host on a non-production `ENVIRONMENT`).

The database stays **external** (your current MySQL) — configured only in
`backend/.env` on the EC2. There is no MySQL container in production.

---

## 1. One-time: GitHub repository secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
|--------|-------|
| `EC2_HOST` | EC2 public IP (e.g. `1.2.3.4`) |
| `EC2_USER` | SSH user — `ubuntu` (Ubuntu AMI) or `ec2-user` (Amazon Linux) |
| `SSH_PRIVATE_KEY` | **Full contents** of the private key (`.pem`) you SSH in with |
| `EC2_APP_DIR` | App directory on the host, e.g. `/home/ubuntu/app` |
| `GHCR_USER` | The GitHub **username** that owns `GHCR_PAT` (e.g. `9741Prajwalj`) — **not** the org `shopwellvia-art` |
| `GHCR_PAT` | A GitHub **classic PAT** with the `read:packages` scope (used by the EC2 to pull images) |

> `GHCR_PAT`: GitHub → Settings → Developer settings → Personal access tokens →
> Tokens (classic) → Generate, tick **`read:packages`**. If the
> `shopwellvia-art` org has SSO enabled, click **Configure SSO → Authorize** on
> the token afterwards or GHCR answers `denied`. Fine-grained PATs do **not**
> work here — it must be a classic token. (Pushing from Actions uses the
> built-in `GITHUB_TOKEN`; the PAT is only for the EC2 to pull.)

---

## 2. One-time: EC2 host setup

SSH into the instance and run:

```bash
# --- Docker + compose plugin (Ubuntu) ---
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER      # then log out & back in (or: newgrp docker)

# --- App directory + production env ---
mkdir -p ~/app/backend
cd ~/app
# Create backend/.env from the template in the repo and FILL IT IN:
nano backend/.env        # start from backend/.env.example, set real values
```

Fill `backend/.env` (see `backend/.env.example`). Critical values:
- `SECRET_KEY` → run `openssl rand -hex 32` and paste the result
- `MYSQL_*` → **your existing DB connection** (keep current values)
- `MEDIA_BASE_URL`, `CORS_ORIGINS`, `FRONTEND_URL` → `http://<EC2-public-IP>:8090`
- `PAYMENT_RECONCILE_TOKEN` → a random secret shared with the reconcile-cron
  sidecar; run `openssl rand -hex 32`. (Blank disables machine-auth reconcile.)

> Make sure `EC2_APP_DIR` (the secret) matches this directory (`/home/ubuntu/app`).

### Security group
- **Inbound 8090/tcp** open to `0.0.0.0/0` (public web — the published app port).
- **Inbound 22/tcp** open to your IP (SSH).
- The EC2 must be able to reach your MySQL host/port (it already is —
  you're "already connected").

### GHCR package visibility
First successful pipeline run creates two private packages
(`simple-com-backend`, `simple-com-frontend`). The EC2 pulls them using
`GHCR_PAT` (handled automatically by the pipeline's `docker login`). If you'd
rather skip auth, set both packages to **Public** in GitHub → your profile →
Packages → each package → Package settings → Change visibility.

---

## 3. Deploy

```bash
# From the terminal:
git checkout production
git merge --ff-only vinay    # bring in the reviewed work
git push origin production   # tests run first; deploy only fires if they pass
```

Merging a Pull Request into `production` on GitHub does exactly the same thing —
a merge *is* a push, so it triggers the pipeline with no extra step.

> **Working branches no longer run CI.** The pipeline is `production`-only, so a
> push to `vinay`/`main`/anything else does nothing at all. Run the checks
> yourself before merging — see `.github/CICD.md` §9 for the exact commands.

You can also run **CI/CD** manually from the Actions tab (`workflow_dispatch`).
A manual run is gated exactly like a push: it only builds and deploys when it is
run **on the `production` branch** and **both quality jobs pass**. There is no
test-bypass path — to ship, make the tests green.

Watch progress in the repo's **Actions** tab. On success the site is live at:

```
http://<EC2-public-IP>:8090/          # storefront
http://<EC2-public-IP>:8090/docs      # API docs
```

Every deploy is tagged by git SHA in GHCR, so deploys are reproducible.

---

## 4. Verify

```bash
ssh <user>@<EC2-IP>
cd ~/app
docker compose ps                                       # all "Up"/"healthy"
docker compose logs -f backend
curl -fsS http://localhost:8090/api/v1/footer | head -c 200   # API reachable
```

---

## 5. Rollback

Images are tagged by commit SHA. To roll back to a previous good commit:

```bash
ssh <user>@<EC2-IP> && cd ~/app
IMAGE_TAG=<previous-git-sha> docker compose up -d
```

(Or revert the commit on `production` and let the pipeline redeploy.)

---

## 6. Migrations

**The pipeline does NOT run migrations, and you must NOT run `alembic upgrade
head` against the production database.** The shared remote MySQL is on a
migration lineage (`conpay001`) that this repo does not contain, so Alembic
would try to apply the wrong revisions and corrupt the schema.

Schema changes are applied **manually, from reviewed SQL only**:

1. Write the change as plain SQL and get it reviewed (see the examples under
   `backend/scripts/sql/`).
2. Take a backup first — `backend/scripts/backup_db.sh` (see [§7](#7-database-backups)).
3. Apply it against the DB with a MySQL client during a maintenance window.

---

## 7. Database backups

There is no managed backup yet. `backend/scripts/backup_db.sh` is a starting
point: a timestamped, compressed `mysqldump` that reads `MYSQL_*` from the
environment. **A backup that only lives on the EC2 is not a backup** — configure
the S3 upload stub at the bottom of the script and run it on a cron/timer so a
copy survives the instance.

```bash
set -a; . ~/app/backend/.env; set +a     # load MYSQL_* (never printed)
bash ~/app/backend/scripts/backup_db.sh  # writes ./backups/<db>-<ts>.sql.gz
```

---

## 8. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Actions deploy step: `permission denied (publickey)` | `SSH_PRIVATE_KEY` must be the **entire** private key incl. `-----BEGIN/END-----`. `EC2_USER` correct (`ubuntu` vs `ec2-user`). |
| EC2 `docker login` / pull fails with `denied: denied` | `GHCR_USER` must be a GitHub **username**, not the org. `GHCR_PAT` must be a **classic** PAT with `read:packages`, SSO-authorised for the org. Or make both packages Public and drop the login. |
| Site loads but images 404 | `MEDIA_BASE_URL` in `backend/.env` must equal `http://<EC2-IP>:8090`; re-`up -d` the backend. |
| 413 on upload | Already handled (`client_max_body_size 20m` in the frontend nginx) — rebuild/pull the frontend image. |
| Backend can't reach DB | EC2 must reach your MySQL host/port; check the DB firewall/security group and `MYSQL_*` in `backend/.env`. |
| Port 8090 unreachable | Open inbound 8090 in the EC2 security group. |
| Deploy never runs after a push | It is gated on the **frontend** and **backend-tests** jobs passing, and only runs on the `production` branch. Check those two jobs in the **CI/CD** run. |
| **Server shows OLD code / "my changes aren't live"** | Run `bash scripts/deployed-version.sh` on the host. It prints the git SHA baked into each running image and the live `/version` + `/version.json` stamps. If that SHA is behind your commit, the images are stale — usually because the code was **never pushed to `origin/production`** (CI only builds what's on GitHub) or a deploy was skipped. Fix: push to `origin/production` to rebuild via CI, or emergency-rebuild locally (below). |

---

## Emergency: rebuild on the host without CI

The normal path is CI (push to `origin/production`). If you must get the host's
current working-tree code live **right now** without a pipeline run — build the
images locally, stamped with the same provenance CI uses, then recreate:

```bash
cd "$EC2_APP_DIR"            # the checked-out repo on the host
SHA=$(git rev-parse HEAD)
BT=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
docker build --build-arg GIT_SHA="$SHA" --build-arg BUILD_TIME="$BT" \
  -t ghcr.io/shopwellvia-art/simple-com-backend:latest  ./backend
docker build --build-arg GIT_SHA="$SHA" --build-arg BUILD_TIME="$BT" \
  -t ghcr.io/shopwellvia-art/simple-com-frontend:latest ./frontend
docker compose up -d          # recreates onto the new local :latest (no pull)
bash scripts/deployed-version.sh   # confirm the baked SHA == your commit
```

> This is a stop-gap. It does NOT update GHCR or GitHub, so a later CI deploy
> built from an older `origin/production` would overwrite it. Push your commits
> to `origin/production` to make the fix durable and let CI take over again.

---

## TLS termination (required before go-live)

**The app currently serves plain HTTP on port 8090 — there is no TLS anywhere in
this stack.** That is acceptable for staging/testing but MUST be fixed before any
real traffic: login credentials, session tokens and payment return URLs would
otherwise cross the network in cleartext. Until TLS is in place, the HSTS header
in the nginx configs stays commented out (never send HSTS over plain HTTP).

Before go-live, front the app with TLS — do NOT expose 8090 to the public
directly. Options:

- Put a TLS-terminating reverse proxy (Caddy with automatic Let's Encrypt, or
  nginx + certbot) in front, proxying `https://<domain>` → `127.0.0.1:8090`.
- Or terminate TLS at an AWS ALB / CloudFront in front of the instance.

Then point the domain's DNS A-record at the EC2 IP, update `MEDIA_BASE_URL`,
`CORS_ORIGINS`, `FRONTEND_URL` to `https://<domain>`, uncomment the
`Strict-Transport-Security` header in the nginx configs, and redeploy.
