#!/usr/bin/env bash
#
# backup_db.sh — timestamped, compressed mysqldump of the app database.
#
# This is a STARTING POINT for the (currently missing) DB backup story. The
# production database is the shared REMOTE MySQL, so a lost/overwritten table has
# no recovery path today. Run this on a schedule (cron / systemd timer) and, most
# importantly, SHIP THE OUTPUT OFF-HOST — a backup that only lives on the EC2 is
# gone the moment the instance is. An S3 upload is stubbed at the bottom.
#
# Connection params come from the environment, using the SAME names the backend
# reads (see backend/app/core/config.py). The simplest way to run it with the
# real values is to source the deployed env file first:
#
#     set -a; . /home/ubuntu/app/backend/.env; set +a
#     bash backend/scripts/backup_db.sh
#
# Required env: MYSQL_HOST MYSQL_USER MYSQL_PASSWORD MYSQL_DB
# Optional env: MYSQL_PORT (default 3306)  BACKUP_DIR (default ./backups)
#
# NOTE: this only reads (mysqldump); it never modifies the database. It also
# never echoes the password — it's passed via a temporary defaults-file so it
# does not appear in the process list (`ps`).

set -euo pipefail

# --- Resolve config -----------------------------------------------------------
MYSQL_HOST="${MYSQL_HOST:?set MYSQL_HOST (e.g. source backend/.env first)}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQL_USER="${MYSQL_USER:?set MYSQL_USER}"
MYSQL_PASSWORD="${MYSQL_PASSWORD:?set MYSQL_PASSWORD}"
MYSQL_DB="${MYSQL_DB:?set MYSQL_DB}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

TIMESTAMP="$(date -u +%Y%m%d-%H%M%SZ)"
OUTFILE="${BACKUP_DIR}/${MYSQL_DB}-${TIMESTAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"

# --- Keep the password out of argv / the process list -------------------------
# mysqldump reads credentials from this defaults-file instead of the command
# line, so `ps aux` never shows the password. Cleaned up on exit.
DEFAULTS_FILE="$(mktemp)"
trap 'rm -f "${DEFAULTS_FILE}"' EXIT
cat >"${DEFAULTS_FILE}" <<EOF
[client]
host=${MYSQL_HOST}
port=${MYSQL_PORT}
user=${MYSQL_USER}
password=${MYSQL_PASSWORD}
EOF

echo "Dumping ${MYSQL_DB} from ${MYSQL_HOST}:${MYSQL_PORT} -> ${OUTFILE}"

# --single-transaction : consistent snapshot without locking (InnoDB), so the
#                        live app is not blocked during the dump.
# --quick              : stream rows instead of buffering the whole table.
# --routines/--triggers/--events : include stored programs, not just data.
# --no-tablespaces     : avoids needing the PROCESS privilege on managed MySQL.
mysqldump \
  --defaults-extra-file="${DEFAULTS_FILE}" \
  --single-transaction \
  --quick \
  --routines \
  --triggers \
  --events \
  --no-tablespaces \
  "${MYSQL_DB}" \
  | gzip -c > "${OUTFILE}"

echo "Backup written: ${OUTFILE} ($(du -h "${OUTFILE}" | cut -f1))"

# --- Ship off-host (REQUIRED for real durability) -----------------------------
# A backup sitting next to the database is not a backup. Uncomment and configure
# one of these so a copy lives somewhere the EC2 dying can't take with it:
#
#   aws s3 cp "${OUTFILE}" "s3://${BACKUP_S3_BUCKET:?}/db/${MYSQL_DB}/${TIMESTAMP}.sql.gz"
#
# Consider S3 lifecycle rules / versioning for retention, and encrypt at rest.
