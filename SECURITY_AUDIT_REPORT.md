# Security Audit & Remediation Report

**Project:** simple-ecomers (FastAPI backend + React/Vite frontend)
**Method:** Multi-agent cybersecurity team — recon (surface map + secrets/deps scan), 7 specialist pentest auditors, adversarial double-verification of every finding (false positives dropped), then automated + manual remediation grouped by disjoint file ownership.
**Result:** 151 routes mapped · **35 confirmed vulnerabilities** (3 Critical · 8 High · 7 Medium · 13 Low · 4 Info).

**Status: code remediation COMPLETE.** All modified Python compiles; frontend production build passes; the URL-scheme validator passes its unit tests. Remaining items are (a) intentional deferrals that would break existing data/UX, and (b) **manual owner actions** that cannot be automated (DB password rotation, git-history purge, TLS rollout).

---

## 🔴 CRITICAL (3)

| ID | Finding | Code fix | Manual action still required |
|----|---------|----------|------------------------------|
| C1 | Live prod MySQL password `Vinay@1234#` @ `<DB_HOST>:3306` hardcoded in git-tracked `scripts/check_db.py`; DB internet-exposed | ✅ Script reads env only; literals removed | **ROTATE the DB password**, firewall port 3306 to the backend host, **purge from git history** |
| C2 | Same password/host leaked in `vinay.md`, `PROJECT_KT.md`, `.claude/agents/db-agent.md`, `.claude/memory/project-context.md` | ✅ All 4 docs scrubbed (`git grep` confirms zero live values) | **Purge from git history** (BFG/filter-repo + force-push) |
| C3 | `SECRET_KEY` = literal placeholder → PASETO auth tokens forgeable, Fernet PII decryptable | ✅ Real 64-hex key in `.env`; production boot-guard refuses placeholder; keys now domain-separated | Set the real key on the **server** `.env`; rotation logs users out + invalidates stored TOTP (expected) |

## 🟠 HIGH (8) — all fixed in code

| Finding | Fix |
|---------|-----|
| Google OAuth trusted **unverified** email → account takeover | ✅ `email_verified` normalized in `google.py`, enforced in `auth_service.login_with_google` |
| PASETO token key + Fernet at-rest key = one unsalted SHA-256 of placeholder | ✅ `_derive_key(label)` domain separation; key now real |
| Stored XSS via unrestricted upload extension (filename overrode content-type) | ✅ `storage/local.py` + `s3.py` derive ext **only** from validated content-type allowlist; reject unknown |
| Mock payment webhook could mark any order PAID | ✅ Disabled when `ENVIRONMENT=production` |
| `SECRET_KEY`/PASETO key shipped as placeholder | ✅ See C3 |
| **Access tokens couldn't be revoked** server-side (force-logout cosmetic) | ✅ `iat` added to access tokens; `revoke_all_for_user` stamps `revoked_after`; `get_current_user` rejects pre-revocation tokens (fails open on Redis outage) |
| Entire stack over plaintext HTTP — no TLS | 🟡 Guidance added to `.env.production.example` + commented HSTS in nginx — **TLS rollout is a deployment action** |

## 🟡 MEDIUM (7) — all fixed in code

| Finding | Fix |
|---------|-----|
| Reset-OTP cleartext in Redis + non-constant-time compare | ✅ SHA-256 hashed, `hmac.compare_digest`, 5-attempt cap, TTL preserved |
| Console email backend logged the cleartext OTP | ✅ Body redacted, metadata-only log |
| Stock decrement TOCTOU (oversell) | ✅ Atomic `UPDATE … WHERE stock >= qty`; 0 rows → out-of-stock |
| PhonePe webhook didn't verify paid amount vs order total | ✅ `_apply_status` compares gateway paise to order total; mismatch → stays PENDING |
| Stored XSS via `javascript:` URLs in admin-managed pages | ✅ Backend Pydantic scheme-validators on all footer/site-page/hero URL fields **+** frontend `safeUrl()` on every raw `href` sink |
| No security response headers | ✅ Backend `SecurityHeadersMiddleware` (nosniff/frame/referrer/CSP/HSTS) + nginx headers on all 3 configs + `/media` sandbox |
| OpenAPI `/docs` & `/openapi.json` public in production | ✅ `docs_url/redoc_url/openapi_url=None` when `ENVIRONMENT=production` |

## 🟢 LOW (13)

✅ Fixed: `decode_token` now **requires** `exp` · reset-OTP constant-time · open-redirect on `PaymentMockPage` validated · `/forgot-password` + `/reset-password` per-IP rate limits · TOTP-confirm now uses a typed schema · nginx `server_tokens off` (all configs) · CORS wildcard+credentials guard.
⏸️ Deferred (intentional — would break existing data/UX, `# SECURITY TODO` left in code): bcrypt 72-byte pre-hash & passlib→argon2 (invalidate existing password hashes); OAuth tokens-in-URL-fragment redesign (needs coordinated frontend change).
📌 Recommended next (business-logic hardening, not yet implemented): checkout idempotency key · bind COD-OTP to order amount/items · coupon-stacking clamp.

## ℹ️ INFO (4)
✅ **No SQL-injection sinks** and **IDOR correctly enforced everywhere** (positive findings) · CORS guard added · frontend dep floors mitigated by committed lockfile (no action).

---

## ✅ Verification performed
- `python -m py_compile` on **all** modified backend files → pass.
- Frontend `npm run build` → pass (6.9s, no errors). *(Note: `npm run lint` is broken project-wide — ESLint 9 needs an `eslint.config.js` the repo lacks; pre-existing, unrelated to these changes.)*
- URL safe-scheme validator unit-tested (`javascript:`, `java\tscript:`, `data:`, `//host`, casing tricks all rejected; http/https/mailto/tel/relative allowed) → pass.
- ⚠️ Full backend `pytest` could **not** run locally: the machine's global Python 3.14 has an incompatible pydantic (can't import `TypeAdapter`) and there's no project venv. Run the suite in Docker, or `python -m venv` + `pip install -r backend/requirements.txt` on a supported Python, then `pytest backend/tests`.

## 🚨 Manual owner runbook (cannot be automated)
1. **Rotate the MySQL password now** + create a least-privilege app user — treat `Vinay@1234#` as fully compromised.
2. **Firewall port 3306** to the backend host only (not the public internet).
3. **Purge secrets from git history**: BFG / `git filter-repo` to strip the password, host IP, and old `check_db.py` literals, then force-push. *(Deliberately not automated — destructive/irreversible.)*
4. Deploy the new `SECRET_KEY` to the server `.env`; users re-authenticate; re-enroll TOTP users.
5. Stand up TLS (nginx/ALB), switch URLs to `https://`, and uncomment HSTS in `docker/nginx/default.conf`.
6. Review/remove the stray `docker-compose.override.yml` (a fix-agent created it; remaps frontend → :5174).

## Files changed
37 tracked files modified + new `SECURITY_AUDIT_REPORT.md`, `backend/app/schemas/_validators.py`, `frontend/src/lib/safeUrl.js`. Review with `git diff`.
