"""One-time repair: strip surrounding whitespace from stored gateway credentials.

Why this exists: until 2026-08-03 the admin panel stored pasted credential
values verbatim, so a trailing newline/space poisoned the secret and the
gateway rejected every call with an auth error (live incident: Razorpay 401
BAD_REQUEST_ERROR "Authentication failed" from one trailing character on
key_secret). PaymentMethodConfigService.update now strips on save; this script
repairs rows written before that fix.

Idempotent and read-mostly: only rows whose decrypted values change under
str.strip() are rewritten. Prints value lengths only — never a secret.

Run inside the backend container (which holds the right SECRET_KEY and DB env):

    docker cp backend/scripts/repair_payment_method_whitespace.py \
        well-via-ecomm-backend-1:/tmp/repair_pm_ws.py
    docker exec well-via-ecomm-backend-1 python /tmp/repair_pm_ws.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/app")

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.crypto import decrypt_secret, encrypt_secret


def main() -> None:
    eng = create_engine(settings.DATABASE_URL)
    repaired = 0
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT gateway_code, credentials_encrypted FROM payment_methods "
            "WHERE credentials_encrypted <> ''"
        )).all()
        for code, blob in rows:
            try:
                creds = json.loads(decrypt_secret(blob))
            except Exception as exc:  # noqa: BLE001 - report, don't abort the sweep
                print(f"{code}: cannot decrypt ({exc}) — skipped")
                continue
            trimmed = {k: v.strip() for k, v in creds.items() if v.strip()}
            if trimmed == creds:
                continue
            before = {k: len(v) for k, v in creds.items()}
            after = {k: len(v) for k, v in trimmed.items()}
            conn.execute(
                text(
                    "UPDATE payment_methods SET credentials_encrypted = :blob "
                    "WHERE gateway_code = :code"
                ),
                {
                    "blob": encrypt_secret(json.dumps(trimmed)) if trimmed else "",
                    "code": code,
                },
            )
            repaired += 1
            print(f"{code}: repaired — value lengths {before} -> {after}")
    print(f"done: {repaired} row(s) repaired" if repaired else "done: nothing to repair")


if __name__ == "__main__":
    main()
