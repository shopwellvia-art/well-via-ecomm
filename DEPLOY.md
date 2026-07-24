# Deployment — CI/CD to AWS EC2 (GitHub Actions + GHCR)

Push to `production` → the **Backend tests** workflow runs, and **only if it
passes** does the deploy pipeline build the backend & frontend Docker images,
push them to **GHCR**, then SSH into your **EC2** to pull the new images and
restart the stack. The app is served over **plain HTTP on port 8090** of the
EC2's public IP (see [TLS termination](#tls-termination-required-before-go-live)).

Database schema changes are **NOT** applied by the pipeline — they are done
manually from reviewed SQL (see [§6](#6-migrations)).

```
GitHub push (production)
   └─ Actions: Backend tests ── (must pass) ──┐
   └─ Actions: build backend img ─┐           │ (gated via workflow_run)
                build frontend img ┘→ push to ghcr.io
   └─ Actions: ssh → EC2 → docker compose pull + up -d   (no alembic — see §6)
EC2 host:  [frontend+nginx :8090→:80] ─proxy─> [backend :8000] ─> shared MySQL
                                                 [redis]           (external)
                                       [payment-reconcile-cron] ──┘ (every ~10m)
```

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
| `GHCR_PAT` | A GitHub **classic PAT** with the `read:packages` scope (used by the EC2 to pull images) |

> `GHCR_PAT`: GitHub → Settings → Developer settings → Personal access tokens →
> Tokens (classic) → Generate, tick **`read:packages`**. (Pushing from Actions
> uses the built-in `GITHUB_TOKEN`; the PAT is only for the EC2 to pull.)

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
nano backend/.env        # paste backend/.env.production.example, set real values
```

Fill `backend/.env` (see `backend/.env.production.example`). Critical values:
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
git push origin production   # tests run first; deploy only fires if they pass
```

You can also run **Build & Deploy to EC2** manually from the Actions tab — a
manual (`workflow_dispatch`) run deliberately **bypasses the test gate**, for
emergency redeploys of an already-tested commit.

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
docker compose -f docker-compose.deploy.yml ps          # all "Up"/"healthy"
docker compose -f docker-compose.deploy.yml logs -f backend
curl -fsS http://localhost:8090/api/v1/footer | head -c 200   # API reachable
```

---

## 5. Rollback

Images are tagged by commit SHA. To roll back to a previous good commit:

```bash
ssh <user>@<EC2-IP> && cd ~/app
IMAGE_TAG=<previous-git-sha> docker compose -f docker-compose.deploy.yml up -d
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
| EC2 `docker login` / pull fails | `GHCR_PAT` needs `read:packages`; or make the packages Public. |
| Site loads but images 404 | `MEDIA_BASE_URL` in `backend/.env` must equal `http://<EC2-IP>:8090`; re-`up -d` the backend. |
| 413 on upload | Already handled (`client_max_body_size 20m` in the frontend nginx) — rebuild/pull the frontend image. |
| Backend can't reach DB | EC2 must reach your MySQL host/port; check the DB firewall/security group and `MYSQL_*` in `backend/.env`. |
| Port 8090 unreachable | Open inbound 8090 in the EC2 security group. |
| Deploy never runs after a push | It is gated on **Backend tests** passing (see [§Deploy](#3-deploy)); a red test run blocks it. Check the tests workflow, or trigger a manual run. |

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
