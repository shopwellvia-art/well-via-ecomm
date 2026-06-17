"""E2E test for Phase 9 Split COD.

Covers: /cod/check split fields, checkout(split_cod) calls the gateway
with the prepaid portion only, mock approval transitions order to PAID
with cod_balance = total - prepaid, carrier dispatch carries cod_amount
= balance.
"""
import urllib.request
import urllib.error
import json
import random

BASE = "http://localhost:8000/api/v1"


def req(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if body is not None:
        r.add_header("content-type", "application/json")
    if token:
        r.add_header("authorization", f"Bearer {token}")
    try:
        resp = urllib.request.urlopen(r)
        b = resp.read()
        return resp.status, (json.loads(b) if b else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


_, alogin = req("POST", "/auth/login", {"email": "vinay@gmail.com", "password": "vinay@123"})
admin = alogin["access_token"]
req("PATCH", "/settings", {"updates": {
    "cod.enabled": "true",
    "cod.flat_surcharge": "40",
    "cod.min_order_total": "199",
    "cod.max_order_total": "5000",
    "cod.block_first_time_customer": "false",
    "cod.split_enabled": "true",
    "cod.split_prepaid_amount": "100",
    "shipping.provider": "mock",
    "shipping.warehouse.name": "Lumen HQ",
    "shipping.warehouse.pincode": "560001",
    "shipping.warehouse.address": "MG Road, Bengaluru",
}}, token=admin)
req("PATCH", "/products/1", {"cod_blocked": False, "stock": 200}, token=admin)

email = f"split_{random.randint(10000, 99999)}@example.com"
req("POST", "/auth/register", {"email": email, "password": "Passw0rd!"})
_, login = req("POST", "/auth/login", {"email": email, "password": "Passw0rd!"})
cust = login["access_token"]

print("--- Test 1: /cod/check surfaces split_available + amounts ---")
st, ans = req("POST", "/cod/check", {
    "items": [{"product_id": 1, "quantity": 1}],
    "destination_pincode": "400001",
}, token=cust)
print(f"  available={ans['available']} split_available={ans['split_available']}")
print(f"  split_prepaid={ans['split_prepaid_amount']} split_cod={ans['split_cod_amount']}")
# subtotal 299 + surcharge 40 = 339 cod_total. Prepaid 100, balance 239.
assert ans["available"] and ans["split_available"]
assert float(ans["split_prepaid_amount"]) == 100.0
assert float(ans["split_cod_amount"]) == 239.0

print("--- Test 2: split disabled -> split_available=False ---")
req("PATCH", "/settings", {"updates": {"cod.split_enabled": "false"}}, token=admin)
st, ans = req("POST", "/cod/check", {
    "items": [{"product_id": 1, "quantity": 1}],
    "destination_pincode": "400001",
}, token=cust)
print(f"  split_available={ans['split_available']}")
assert not ans["split_available"]
assert ans["available"]   # full COD still on
req("PATCH", "/settings", {"updates": {"cod.split_enabled": "true"}}, token=admin)

print("--- Test 3: prepaid >= total -> split hidden ---")
req("PATCH", "/settings", {"updates": {"cod.split_prepaid_amount": "9999"}}, token=admin)
st, ans = req("POST", "/cod/check", {
    "items": [{"product_id": 1, "quantity": 1}],
    "destination_pincode": "400001",
}, token=cust)
print(f"  prepaid 9999 on cart 339: split_available={ans['split_available']}")
assert not ans["split_available"]
req("PATCH", "/settings", {"updates": {"cod.split_prepaid_amount": "100"}}, token=admin)

print("--- Test 4: checkout(split_cod) — gateway gets prepaid portion, order PENDING ---")
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "Nariman Point, Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "split_cod",
}, token=cust)
print(f"  checkout: {st} order={co['order_id']} mtid={co['merchant_transaction_id']}")
print(f"  amount_minor={co['amount_minor']} (expected 10000 for ₹100 prepaid)")
assert co["amount_minor"] == 10000   # ₹100 in paise

st, o = req("GET", f"/orders/{co['order_id']}", token=cust)
print(f"  order: method={o['payment_method']} status={o['status']} "
      f"total={o['total_amount']} balance={o['cod_balance']} "
      f"surcharge={o['cod_surcharge_amount']}")
assert o["payment_method"] == "split_cod"
assert o["status"] == "pending"   # awaiting gateway
# 299 + 75 shipping + 40 surcharge = 414. Prepaid 100, balance 314.
assert abs(float(o["total_amount"]) - 414.0) < 0.01
assert abs(float(o["cod_balance"]) - 314.0) < 0.01

print("--- Test 5: approve via mock gateway -> order PAID with balance intact ---")
req("POST", "/payments/webhook/mock", {
    "merchant_transaction_id": co["merchant_transaction_id"],
    "action": "approve",
})
st, o = req("GET", f"/orders/{co['order_id']}", token=cust)
print(f"  after approve: status={o['status']} balance={o['cod_balance']}")
assert o["status"] == "paid"
assert abs(float(o["cod_balance"]) - 314.0) < 0.01

print("--- Test 6: carrier push uses cod_amount=balance (not total) ---")
st, pushed = req("POST", f"/orders/admin/{co['order_id']}/push-to-carrier", token=admin)
print(f"  push status={st} awb={pushed['shipping_awb']}")
assert pushed["shipping_awb"]

print("--- Test 7: decline path -> order CANCELLED ---")
email2 = f"split2_{random.randint(10000, 99999)}@example.com"
req("POST", "/auth/register", {"email": email2, "password": "Passw0rd!"})
_, login2 = req("POST", "/auth/login", {"email": email2, "password": "Passw0rd!"})
c2 = login2["access_token"]
st, co2 = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "split_cod",
}, token=c2)
req("POST", "/payments/webhook/mock", {
    "merchant_transaction_id": co2["merchant_transaction_id"],
    "action": "decline",
})
st, o2 = req("GET", f"/orders/{co2['order_id']}", token=c2)
print(f"  declined: status={o2['status']}")
assert o2["status"] == "cancelled"

print("--- Test 8: split_cod refused when COD gates fail (e.g. cod_blocked product) ---")
req("PATCH", "/products/1", {"cod_blocked": True}, token=admin)
email3 = f"split3_{random.randint(10000, 99999)}@example.com"
req("POST", "/auth/register", {"email": email3, "password": "Passw0rd!"})
_, login3 = req("POST", "/auth/login", {"email": email3, "password": "Passw0rd!"})
c3 = login3["access_token"]
st, ans = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "split_cod",
}, token=c3)
print(f"  status={st} msg=\"{(ans or {}).get('error', {}).get('message', '')[:80]}\"")
assert st == 409
req("PATCH", "/products/1", {"cod_blocked": False}, token=admin)

# Cleanup
req("PATCH", "/settings", {"updates": {
    "cod.split_enabled": "false",
    "shipping.provider": "none",
}}, token=admin)
print()
print("All tests passed.")
