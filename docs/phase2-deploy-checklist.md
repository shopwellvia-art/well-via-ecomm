# Phase 2 — host-side deploy checklist

Operator actions that **cannot be done from this repo**: they touch the EC2's
`backend/.env` (never committed), the Docker volumes, the security groups, the
external MySQL, or git history. Companion docs: `DEPLOY.md` (pipeline + host
layout) and `SECURITY_AUDIT_REPORT.md` (owner runbook, items 1-5).

Conventions below: the app directory on the EC2 is `~/app` (whatever
`EC2_APP_DIR` is set to), so the live env file is `~/app/backend/.env` and all
`docker compose` commands run from `~/app`.

> Order matters for the first two: set the real `SECRET_KEY` (item 6d) **before
> or together with** flipping `ENVIRONMENT=production` (item 1) — the production
> boot-guard in `backend/app/core/config.py` refuses to start with a placeholder
> or short key, which is exactly what you want, but it means flipping the flag
> first bricks the backend until the key is set.

## 1. Set `ENVIRONMENT=production` and `DEBUG=false`

**Why:** the host `.env` currently says `development`, which leaves three
production-only protections switched off:

- `/docs`, `/redoc` and `/openapi.json` stay **public** (`app/main.py` only
  disables them when `ENVIRONMENT=production`).
- The `SECRET_KEY` boot-guard is skipped (`config.py` only validates the key in
  production), so a placeholder key would boot silently.
- The **unsigned mock payment webhook** `POST /api/v1/payments/webhook/mock`
  stays enabled — an anonymous caller can mark any pending order **PAID**. The
  route hard-disables itself only when `ENVIRONMENT=production`
  (`app/api/v1/endpoints/payments.py`).

**Steps:**

```bash
ssh <user>@<EC2-IP>
cd ~/app
nano backend/.env          # set: ENVIRONMENT=production  and  DEBUG=false
docker compose up -d       # env change recreates the backend (and dependents)
# verify all three guards engaged:
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8090/docs                    # expect 404
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' -d '{"merchant_transaction_id":"x","action":"approve"}' \
  http://localhost:8090/api/v1/payments/webhook/mock                                   # expect 403
docker compose logs backend | tail -20                                                 # booted, no SECRET_KEY error
```

## 2. Set `PAYMENT_RECONCILE_TOKEN`

**Why:** the `payment-reconcile-cron` sidecar (see `docker-compose.yml`) POSTs
`/api/v1/payments/admin/reconcile-pending` every ~10 minutes with the
`X-Reconcile-Token` header; the endpoint 403s without a matching value. Blank
token = machine auth disabled, so missed PhonePe webhooks leave orders stuck
"pending" forever. Both the backend and the sidecar read the **same**
`backend/.env` (`env_file`), so one value keeps them in sync.

**Steps:**

```bash
openssl rand -hex 32                       # generate the secret
nano ~/app/backend/.env                    # PAYMENT_RECONCILE_TOKEN=<paste it>
cd ~/app && docker compose up -d           # recreates backend + payment-reconcile-cron
docker compose logs -f payment-reconcile-cron   # no "reconcile POST failed" lines
```

## 3. Switch email from `console` to real SMTP

**Why:** `EMAIL_BACKEND=console` only logs that an email would have been sent —
no OTPs, password resets, or order emails actually reach customers.

**Steps:**

```bash
nano ~/app/backend/.env
# EMAIL_BACKEND=smtp
# SMTP_HOST=..., SMTP_PORT=587, SMTP_USER=..., SMTP_PASSWORD=..., SMTP_USE_TLS=true
# EMAIL_FROM=<real sender address>
cd ~/app && docker compose up -d
```

**Verify:** log in to the admin panel → **Settings** → send a test email
(`POST /api/v1/settings/test-email`, requires `settings.manage`) and confirm it
arrives — including at a non-same-domain inbox (Gmail etc.) to catch SPF/DKIM
problems.

## 4. Migrate the 13 legacy flat-layout uploads

**Why:** the current storage backend saves under entity/date folders
(`products/<yyyy>/<mm>/<id>.jpg`), but 13 legacy files (12 product/hero JPGs +
1 test PDF) sit **flat** in the repo's `backend/uploads/` and are referenced in
the DB as `/media/<flat-filename>`. The production `backend_uploads` volume does
not contain them, so those URLs 404 today.

**Steps:**

```bash
# from your machine, in the repo root:
scp "backend/uploads/"*.jpg "backend/uploads/"*.pdf <user>@<EC2-IP>:/tmp/legacy-uploads/

# on the EC2 — copy into the volume root through the running backend container:
ssh <user>@<EC2-IP>
cd ~/app
docker compose cp /tmp/legacy-uploads/. backend:/app/uploads/
rm -r /tmp/legacy-uploads

# verify one of them serves (any filename from backend/uploads/):
curl -s -o /dev/null -w '%{http_code}\n' \
  http://localhost:8090/media/108aae6a273b47b4af46d8911eea7d29.jpg   # expect 200
```

The files must land in the volume **root** (not a subfolder) because that is the
path the legacy DB rows reference.

## 5. TLS in front of port 8090

**Why:** the stack serves plain HTTP on `:8090` — credentials, session tokens
and payment return URLs cross the network in cleartext. `DEPLOY.md` marks TLS
as **required before go-live**.

**Important host constraint:** per `docker-compose.yml`, the Omvedha nginx on
this EC2 already owns `:80`/`:443` (and the LAMP stack owns `:8080`). A new
Caddy cannot bind those ports — so either add a vhost to the **existing** host
nginx, or terminate TLS at an ALB/CloudFront. Caddy is only an option on a host
where 80/443 are free.

**Steps (existing host nginx + certbot):**

```bash
sudo tee /etc/nginx/sites-available/wellvia <<'EOF'
server {
    server_name <your-domain>;
    listen 80;
    location / {
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 20m;      # match the app nginx upload limit
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/wellvia /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d <your-domain>          # obtains cert, adds the 443 block
```

**Then:** point the domain's DNS A-record at the EC2 IP; in
`~/app/backend/.env` set `MEDIA_BASE_URL`, `CORS_ORIGINS`, `FRONTEND_URL` to
`https://<your-domain>`; uncomment the `Strict-Transport-Security` header in
`frontend/nginx.conf`; redeploy (push to `production` or `docker compose up -d`
after the env edit). Finally, close inbound `8090` in the security group to
everything except the host itself so traffic can only enter via TLS.

## 6. Still-open items from the security audit owner runbook

From `SECURITY_AUDIT_REPORT.md` ("Manual owner runbook") — the code fixes
landed, these operational actions did not:

**a. Rotate the MySQL password** — the old one (see audit finding C1) was
committed to git and must be treated as fully compromised.

```sql
-- on the MySQL host, as an admin user:
ALTER USER 'ecom'@'%' IDENTIFIED BY '<new-strong-password>';
-- better: create a least-privilege app user instead of reusing a broad one
FLUSH PRIVILEGES;
```

Then update `MYSQL_PASSWORD` in `~/app/backend/.env` and
`cd ~/app && docker compose up -d`. Take a backup first
(`backend/scripts/backup_db.sh`, see `DEPLOY.md` §7).

**b. Firewall port 3306** — the DB is internet-exposed. In the DB host's
security group / firewall, restrict inbound `3306/tcp` to the backend EC2's IP
(or security group) only. Verify from an outside machine:
`nc -zv <DB-HOST> 3306` should now time out.

**c. Purge leaked secrets from git history** — scrubbing the working tree (done)
does not remove the password, DB host IP, and old `scripts/check_db.py`
literals from history.

```bash
pip install git-filter-repo
git clone --mirror <repo-url> repo-mirror && cd repo-mirror
git filter-repo --replace-text <(printf '%s\n' '<leaked-password>==>REMOVED' '<db-host-ip>==>REMOVED')
git push --force --all && git push --force --tags
```

Every collaborator must re-clone afterwards. History purge is **not** a
substitute for rotation — do (a) regardless.

**d. Deploy a fresh `SECRET_KEY`** — the server must not run on any key that
ever appeared in git.

```bash
openssl rand -hex 32                      # generate
nano ~/app/backend/.env                   # SECRET_KEY=<paste it>
cd ~/app && docker compose up -d
```

Expected side effects (per audit C3): all users are logged out and
re-authenticate; stored TOTP secrets are invalidated — TOTP users must
re-enroll.

---

Done when: `/docs` 404s, the mock webhook 403s, reconcile-cron logs are clean,
a test email arrives, a legacy `/media/...jpg` URL returns 200, the site is
`https://` only, port 3306 is closed to the internet, and the old DB password
no longer works.
