"""One-shot E2E test for Phase 7 returns flow.

Run inside the backend container:
    docker compose exec -T backend python scripts/test_returns_e2e.py
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
    "shipping.provider": "mock",
    "shipping.warehouse.name": "Lumen HQ",
    "shipping.warehouse.pincode": "560001",
    "shipping.warehouse.address": "MG Road, Bengaluru",
}}, token=admin)

email = f"ret_{random.randint(10000, 99999)}@example.com"
req("POST", "/auth/register", {"email": email, "password": "Passw0rd!"})
_, login = req("POST", "/auth/login", {"email": email, "password": "Passw0rd!"})
cust = login["access_token"]
_, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "CP, 110001",
    "shipping_pincode": "110001",
}, token=cust)
order_id = co["order_id"]
req("POST", "/payments/webhook/mock", {"merchant_transaction_id": co["merchant_transaction_id"], "action": "approve"})
_, pushed = req("POST", f"/orders/admin/{order_id}/push-to-carrier", token=admin)
req("POST", f"/shipping/mock/simulate?awb={pushed['shipping_awb']}&status=delivered", token=admin)
_, o = req("GET", f"/orders/admin/{order_id}", token=admin)
order_item_id = o["items"][0]["id"]
print(f"Setup: order #{order_id} delivered, item={order_item_id} qty=2")

print()
print("--- Test 1: customer can request a return ---")
st, ret = req("POST", "/returns", {
    "order_id": order_id,
    "items": [{"order_item_id": order_item_id, "quantity": 1}],
    "reason": "defective",
    "customer_notes": "Stopped working after a day",
}, token=cust)
return_id = ret["id"]
print(f"  status={st} return_id={return_id} state={ret['status']}")
assert ret["status"] == "requested"

print("--- Test 2: cannot return more than purchased ---")
st, ans = req("POST", "/returns", {
    "order_id": order_id,
    "items": [{"order_item_id": order_item_id, "quantity": 5}],
    "reason": "defective",
}, token=cust)
print(f"  qty>order: {st} {(ans or {}).get('error', {}).get('message', '')[:60]}")
assert st in (409, 422)

print("--- Test 3: bad reason rejected ---")
st, ans = req("POST", "/returns", {
    "order_id": order_id,
    "items": [{"order_item_id": order_item_id, "quantity": 1}],
    "reason": "i_just_want_to",
}, token=cust)
print(f"  bad reason: {st} {(ans or {}).get('error', {}).get('message', '')[:60]}")
assert st == 422

print("--- Test 4: admin sees the return ---")
st, items = req("GET", "/returns/admin", token=admin)
print(f"  admin list: {st} count={len(items)}")
assert any(r["id"] == return_id for r in items)

print("--- Test 5: admin approves -> reverse AWB minted ---")
st, ret = req("POST", f"/returns/admin/{return_id}/approve", {"admin_notes": "Looks legit"}, token=admin)
print(f"  status={st} state={ret['status']} reverse_awb={ret['reverse_awb']} refund_amount={ret['refund_amount']}")
assert ret["status"] == "approved"
assert ret["reverse_awb"], "expected a reverse AWB from mock"
assert float(ret["refund_amount"]) == 299.00

print("--- Test 6: cannot re-approve ---")
st, ans = req("POST", f"/returns/admin/{return_id}/approve", {}, token=admin)
print(f"  re-approve: {st}")
assert st == 409

print("--- Test 7: mark picked_up -> received -> refunded ---")
st, ret = req("POST", f"/returns/admin/{return_id}/mark-picked-up", token=admin)
print(f"  picked_up: {st} state={ret['status']}")
st, ret = req("POST", f"/returns/admin/{return_id}/mark-received", token=admin)
print(f"  received: {st} state={ret['status']}")
st, ret = req("POST", f"/returns/admin/{return_id}/mark-refunded", token=admin)
print(f"  refunded: {st} state={ret['status']}")
assert ret["status"] == "refunded"

print("--- Test 8: second return for the remaining unit ---")
st, ret2 = req("POST", "/returns", {
    "order_id": order_id,
    "items": [{"order_item_id": order_item_id, "quantity": 1}],
    "reason": "no_longer_needed",
}, token=cust)
print(f"  status={st} return_id={ret2['id']} state={ret2['status']}")

print("--- Test 9: cannot return a 3rd unit ---")
st, ans = req("POST", "/returns", {
    "order_id": order_id,
    "items": [{"order_item_id": order_item_id, "quantity": 1}],
    "reason": "defective",
}, token=cust)
print(f"  status={st} msg=\"{(ans or {}).get('error', {}).get('message', '')[:80]}\"")
assert st == 409

print("--- Test 10: customer cancels pending return ---")
st, cancelled = req("POST", f"/returns/{ret2['id']}/cancel", token=cust)
print(f"  cancel: {st} state={cancelled['status']}")
assert cancelled["status"] == "cancelled"

print("--- Test 11: cancelled releases the slot ---")
st, ret3 = req("POST", "/returns", {
    "order_id": order_id,
    "items": [{"order_item_id": order_item_id, "quantity": 1}],
    "reason": "defective",
}, token=cust)
print(f"  status={st} state={ret3['status']}")
assert ret3["status"] == "requested"

print("--- Test 12: admin rejects ---")
st, ret3 = req("POST", f"/returns/admin/{ret3['id']}/reject", {"admin_notes": "Past window"}, token=admin)
print(f"  reject: {st} state={ret3['status']}")
assert ret3["status"] == "rejected"

print("--- Test 13: PENDING order rejects return ---")
email = f"ret2_{random.randint(10000, 99999)}@example.com"
req("POST", "/auth/register", {"email": email, "password": "Passw0rd!"})
_, login = req("POST", "/auth/login", {"email": email, "password": "Passw0rd!"})
c2 = login["access_token"]
_, co2 = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "Test Address, 110001",
    "shipping_pincode": "110001",
}, token=c2)
_, o2 = req("GET", f"/orders/admin/{co2['order_id']}", token=admin)
st, ans = req("POST", "/returns", {
    "order_id": co2["order_id"],
    "items": [{"order_item_id": o2["items"][0]["id"], "quantity": 1}],
    "reason": "defective",
}, token=c2)
print(f"  status={st} msg=\"{(ans or {}).get('error', {}).get('message', '')[:60]}\"")
assert st == 409

print("--- Test 14: window enforcement ---")
req("PATCH", "/settings", {"updates": {"returns.window_days": "0"}}, token=admin)
st, ans = req("POST", "/returns", {
    "order_id": order_id,
    "items": [{"order_item_id": order_item_id, "quantity": 1}],
    "reason": "defective",
}, token=cust)
print(f"  window=0 -> {st}")
assert st == 409
req("PATCH", "/settings", {"updates": {"returns.window_days": "7"}}, token=admin)

# Cleanup
req("PATCH", "/settings", {"updates": {"shipping.provider": "none"}}, token=admin)
print()
print("All tests passed.")
