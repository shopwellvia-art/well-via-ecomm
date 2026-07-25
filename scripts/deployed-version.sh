#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deployed-version.sh — show what code is ACTUALLY live on this host.
#
# Prints, for the backend & frontend containers: the image tag, the git SHA
# baked into the image (OCI label), the HTTP build stamps the live site reports,
# and how they compare to git. Use it to catch "the server is running stale
# images" at a glance instead of diffing files by hand.
#
#   bash scripts/deployed-version.sh
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== running containers =="
docker compose ps --format 'table {{.Name}}\t{{.Image}}\t{{.Status}}' || true
echo

echo "== baked build provenance (OCI labels) =="
for svc in backend frontend; do
  cid="$(docker compose ps -q "$svc" 2>/dev/null || true)"
  if [ -z "$cid" ]; then echo "  $svc: not running"; continue; fi
  img="$(docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || echo '?')"
  rev="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$cid" 2>/dev/null || true)"
  built="$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.created"}}' "$cid" 2>/dev/null || true)"
  printf '  %-9s image=%s  revision=%s  built=%s\n' "$svc" "$img" "${rev:-<none>}" "${built:-<none>}"
done
echo

echo "== HTTP build stamps (what the live site reports on :8090) =="
echo "  frontend: $(curl -fsS http://localhost:8090/version.json 2>/dev/null || echo unreachable)"
echo "  backend : $(curl -fsS http://localhost:8090/version      2>/dev/null || echo unreachable)"
echo

echo "== git =="
echo "  working tree     : $(git rev-parse --short HEAD 2>/dev/null || echo '?')  ($(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'))"
git fetch origin --quiet 2>/dev/null || true
echo "  origin/production: $(git rev-parse --short origin/production 2>/dev/null || echo '?')"
echo
echo "Tip: if the baked 'revision' above differs from what you expect, the live"
echo "images are stale — redeploy via CI (push to origin/production) or, in an"
echo "emergency, rebuild locally (see DEPLOY.md §3 / the emergency-rebuild notes)."
