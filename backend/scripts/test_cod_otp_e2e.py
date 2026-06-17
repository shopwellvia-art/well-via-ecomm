"""E2E for Phase 12 COD OTP.

Uses the admin token for customer-style requests to dodge the /auth/register
rate limit. Reads the dispatched OTP from Redis (mock SMS backend logs it
to console too; reading Redis directly is more reliable for a test)."""
import urllib.request
import urllib.error
import json

import redis

BASE = "http://localhost:8000/api/v1"


def req(m, p, b=None, t=None):
    d = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(f"{BASE}{p}", data=d, method=m)
    if b is not None:
        r.add_header("content-type", "application/json")
    if t:
        r.add_header("authorization", f"Bearer {t}")
    try:
        resp = urllib.request.urlopen(r)
        body = resp.read()
        return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def login(email, pwd):
    body = req("POST", "/auth/login", {"email": email, "password": pwd})[1]
    return body["access_token"]


admin = login("vinay@gmail.com", "vinay@123")
req("PATCH", "/settings", {"updates": {
    "cod.enabled": "true",
    "cod.require_otp": "true",
    "cod.flat_surcharge": "40",
    "cod.min_order_total": "199",
    "cod.max_order_total": "5000",
    "shipping.provider": "mock",
    "shipping.warehouse.name": "Lumen HQ",
    "shipping.warehouse.pincode": "560001",
    "shipping.warehouse.address": "X",
    # Force console SMS so we can read the dispatched code from Redis.
    "sms.backend": "console",
}}, admin)
# Need an admin user id for the OTP keys.
me = req("GET", "/auth/me", None, admin)[1]
admin_uid = me["id"]

R = redis.Redis.from_url("redis://redis:6379/0", decode_responses=True)


def peek_otp_hash(uid, phone):
    # Service stores the hash followed by `:<attempts>`. We just need the
    # hash to assert that *something* was stored.
    return R.get(f"cod:otp:{uid}:{phone}")


print("--- 1. Submit COD with require_otp on + no OTP → 409 ---")
st, ans = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "cod",
    "customer_phone": "+919876543210",
}, admin)
print(f"  status={st} msg={(ans or {}).get('error', {}).get('message', '')[:80]}")
assert st == 409 and "OTP" in (ans or {}).get("error", {}).get("message", "")

print("--- 2. Send OTP — Redis gets a hash, attempts=0 ---")
phone = "+919876543210"
# Clean any prior state from previous runs of the script
R.delete(f"cod:otp:{admin_uid}:{phone}")
R.delete(f"cod:otp_ok:{admin_uid}:{phone}")
R.delete(f"cod:otp_send:{admin_uid}:{phone}")
st, ans = req("POST", "/cod/send-otp", {"phone": phone}, admin)
print(f"  status={st} masked={ans.get('phone_masked')} ttl={ans.get('expires_in_seconds')}")
assert st == 200
raw = peek_otp_hash(admin_uid, phone)
assert raw and ":0" in raw

print("--- 3. Verify with wrong code → 422 + attempts counter advances ---")
st, ans = req("POST", "/cod/verify-otp", {"phone": phone, "code": "000000"}, admin)
print(f"  status={st} msg={(ans or {}).get('error', {}).get('message', '')[:60]}")
assert st == 422
raw2 = peek_otp_hash(admin_uid, phone)
assert raw2 and ":1" in raw2, f"expected attempts=1 in {raw2}"

print("--- 4. Pull the real code via SQLAlchemy-side helper (mock SMS doesn't expose it) ---")
# We can't reverse the hash, but we can call the service in-process to
# trigger a re-send and then immediately read the new hash + reconstruct
# the cleartext via a controlled path. Simpler: re-issue the OTP from
# inside the backend container.
import subprocess
proc = subprocess.run(
    ["python", "-c", f"""
import sys, secrets, hashlib, redis
from app.db.session import SessionLocal
from app.services.cod_otp_service import CodOtpService
db = SessionLocal()
# Force a known code by patching secrets.randbelow temporarily would be
# fiddly. Instead: call .send() and then read the hash, brute-force the
# 6-digit space (10^6) until it matches. ~1s on dev hardware.
svc = CodOtpService(db)
svc.send(user_id={admin_uid}, phone={phone!r})
r = redis.Redis.from_url('redis://redis:6379/0', decode_responses=True)
raw = r.get('cod:otp:{admin_uid}:{phone}')
stored_hash, _attempts = raw.rsplit(':', 1)
for n in range(1_000_000):
    code = f'{{n:06d}}'
    if hashlib.sha256(code.encode()).hexdigest() == stored_hash:
        print(code)
        sys.exit(0)
print('NOT FOUND', file=sys.stderr); sys.exit(1)
"""],
    capture_output=True, text=True,
)
real_code = proc.stdout.strip()
print(f"  recovered real OTP via brute-force: {real_code}")

print("--- 5. Verify with the real code → 200 + verified marker present ---")
st, ans = req("POST", "/cod/verify-otp", {"phone": phone, "code": real_code}, admin)
print(f"  status={st} verified={(ans or {}).get('verified')}")
assert st == 200
assert R.get(f"cod:otp_ok:{admin_uid}:{phone}") is not None

print("--- 6. COD checkout now succeeds (uses the verified marker) ---")
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "cod",
    "customer_phone": phone,
}, admin)
print(f"  status={st} order={co.get('order_id')}")
assert st == 201

print("--- 7. Verified marker is consumed after checkout → second COD attempt blocked ---")
st, ans = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "cod",
    "customer_phone": phone,
}, admin)
print(f"  second attempt status={st}")
assert st == 409  # consumed; needs a fresh OTP

print("--- 8. require_otp=false bypasses the gate entirely ---")
req("PATCH", "/settings", {"updates": {"cod.require_otp": "false"}}, admin)
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "cod",
    "customer_phone": phone,
}, admin)
print(f"  no-OTP COD with require_otp=false: status={st}")
assert st == 201
req("PATCH", "/settings", {"updates": {"cod.require_otp": "true"}}, admin)

# Cleanup
req("PATCH", "/settings", {"updates": {"shipping.provider": "none"}}, admin)
print()
print("All tests passed.")
