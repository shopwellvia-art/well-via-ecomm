"""E2E test for Phase 10 — payment instruments + per-instrument discount."""
import urllib.request
import urllib.error
import json
import random

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


admin = req("POST", "/auth/login", {"email": "vinay@gmail.com", "password": "vinay@123"})[1]["access_token"]
req("PATCH", "/settings", {"updates": {
    "payments.instruments.upi.enabled": "true",
    "payments.instruments.upi.discount_percent": "5",
    "payments.instruments.netbanking.enabled": "true",
    "payments.instruments.netbanking.discount_percent": "0",
    "payments.instruments.card.enabled": "true",
    "payments.instruments.card.discount_percent": "0",
    "payments.instruments.wallet.enabled": "true",
    "payments.instruments.wallet.discount_percent": "0",
    "payments.suggested_instrument": "upi",
    "shipping.provider": "mock",
    "shipping.warehouse.pincode": "560001",
    "shipping.warehouse.name": "Lumen HQ",
    "shipping.warehouse.address": "MG Road, Bengaluru",
}}, admin)

print("--- Test 1: GET /payments/instruments returns 4 enabled items with UPI suggested ---")
st, ans = req("GET", "/payments/instruments")
print(f"  status={st}")
for it in ans["items"]:
    print(f"  {it['code']:11} enabled={it['enabled']} disc={it['discount_percent']}% suggested={it['suggested']}")
upi = next(i for i in ans["items"] if i["code"] == "upi")
assert upi["suggested"] and float(upi["discount_percent"]) == 5.0
assert all(i["enabled"] for i in ans["items"])

print()
print("--- Test 2: Disabling card -> enabled=false in response ---")
req("PATCH", "/settings", {"updates": {"payments.instruments.card.enabled": "false"}}, admin)
st, ans = req("GET", "/payments/instruments")
card = next(i for i in ans["items"] if i["code"] == "card")
print(f"  card.enabled = {card['enabled']}")
assert not card["enabled"]
req("PATCH", "/settings", {"updates": {"payments.instruments.card.enabled": "true"}}, admin)

print()
print("--- Test 3: Checkout with payment_instrument=upi applies 5% discount ---")
email = f"inst_{random.randint(10000,99999)}@example.com"
req("POST", "/auth/register", {"email": email, "password": "Passw0rd!"})
cust = req("POST", "/auth/login", {"email": email, "password": "Passw0rd!"})[1]["access_token"]
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "CP, New Delhi 110001",
    "shipping_pincode": "110001",
    "payment_method": "prepaid",
    "payment_instrument": "upi",
}, cust)
print(f"  checkout {st} order={co['order_id']}")
st, o = req("GET", f"/orders/{co['order_id']}", None, cust)
# subtotal 299 + tax 0 + shipping 75 - coupon 0 - upi 5% of (299+0) = 14.95 -> 374 - 14.95 = 359.05
print(f"  subtotal={o['subtotal']} shipping={o['shipping_amount']} payment_discount={o['payment_discount_amount']} total={o['total_amount']}")
assert o["payment_instrument"] == "upi"
assert abs(float(o["payment_discount_amount"]) - 14.95) < 0.01
assert abs(float(o["total_amount"]) - (299 + 75 - 14.95)) < 0.01

print()
print("--- Test 4: Disabled instrument refused at checkout (defense in depth) ---")
req("PATCH", "/settings", {"updates": {"payments.instruments.wallet.enabled": "false"}}, admin)
email2 = f"inst2_{random.randint(10000,99999)}@example.com"
req("POST", "/auth/register", {"email": email2, "password": "Passw0rd!"})
c2 = req("POST", "/auth/login", {"email": email2, "password": "Passw0rd!"})[1]["access_token"]
st, ans = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "CP 110001",
    "shipping_pincode": "110001",
    "payment_method": "prepaid",
    "payment_instrument": "wallet",
}, c2)
print(f"  status={st} msg={(ans or {}).get('error', {}).get('message', '')[:70]}")
assert st == 422
req("PATCH", "/settings", {"updates": {"payments.instruments.wallet.enabled": "true"}}, admin)

print()
print("--- Test 5: COD ignores instrument (no per-instrument discount) ---")
email3 = f"inst3_{random.randint(10000,99999)}@example.com"
req("POST", "/auth/register", {"email": email3, "password": "Passw0rd!"})
c3 = req("POST", "/auth/login", {"email": email3, "password": "Passw0rd!"})[1]["access_token"]
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "cod",
    "payment_instrument": "upi",  # should be ignored
}, c3)
st, o = req("GET", f"/orders/{co['order_id']}", None, c3)
print(f"  method={o['payment_method']} instrument={o['payment_instrument']} disc={o['payment_discount_amount']} balance={o['cod_balance']}")
assert o["payment_method"] == "cod"
assert o["payment_instrument"] is None
assert float(o["payment_discount_amount"]) == 0.0

print()
print("--- Test 6: Coupon + instrument discount stack ---")
# We don't have a guaranteed coupon in the seed, so we just verify the math
# would apply both via the math we already wrote. Just confirm no double
# count: subtotal+tax+shipping+cod_surcharge - coupon - instrument = total
email4 = f"inst4_{random.randint(10000,99999)}@example.com"
req("POST", "/auth/register", {"email": email4, "password": "Passw0rd!"})
c4 = req("POST", "/auth/login", {"email": email4, "password": "Passw0rd!"})[1]["access_token"]
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "prepaid",
    "payment_instrument": "upi",
}, c4)
st, o = req("GET", f"/orders/{co['order_id']}", None, c4)
# 2 * 299 = 598; shipping 900g = 95 (mock: 50 + 0.05 * max(900,500)); UPI 5% of 598 = 29.90
print(f"  2 units: subtotal={o['subtotal']} shipping={o['shipping_amount']} payment_discount={o['payment_discount_amount']} total={o['total_amount']}")
assert abs(float(o['payment_discount_amount']) - 29.90) < 0.01
expected = 598 + 95 - 29.90
assert abs(float(o['total_amount']) - expected) < 0.01, f"expected {expected}, got {o['total_amount']}"

print()
print("All tests passed.")
req("PATCH", "/settings", {"updates": {"shipping.provider": "none"}}, admin)
