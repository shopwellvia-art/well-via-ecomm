"""E2E test for Phase 11 polish (free-shipping, MRP, cart set-quantity)."""
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


def login(email, password):
    st, body = req("POST", "/auth/login", {"email": email, "password": password})
    if "access_token" not in (body or {}):
        raise SystemExit(f"login failed ({st}): {body}")
    return body["access_token"]


admin = login("vinay@gmail.com", "vinay@123")

req("PATCH", "/settings", {"updates": {
    "shipping.free_threshold": "500",
    "shipping.provider": "mock",
    "shipping.warehouse.pincode": "560001",
    "shipping.warehouse.name": "Lumen HQ",
    "shipping.warehouse.address": "X",
    "cod.flat_surcharge": "40",
    "cod.enabled": "true",
    "cod.split_enabled": "false",
    "payments.instruments.upi.discount_percent": "0",
}}, admin)
req("PATCH", "/products/1", {"stock": 200, "cod_blocked": False, "weight_grams": 450, "compare_at_price": None}, admin)

# Use the admin's token for customer-style requests too — avoids the
# per-IP register rate limit when running this script repeatedly.
c = admin

print("--- 1. 1 unit (₹299 < ₹500) → shipping charged ---")
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 1}],
    "shipping_address": "CP New Delhi 110001",
    "shipping_pincode": "110001",
}, c)
o = req("GET", f"/orders/{co['order_id']}", None, c)[1]
print(f"  shipping={o['shipping_amount']} total={o['total_amount']}")
assert float(o["shipping_amount"]) > 0

print("--- 2. 2 units (₹598 >= ₹500) prepaid → FREE shipping ---")
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "CP New Delhi 110001",
    "shipping_pincode": "110001",
}, c)
o = req("GET", f"/orders/{co['order_id']}", None, c)[1]
print(f"  subtotal={o['subtotal']} shipping={o['shipping_amount']} total={o['total_amount']}")
assert float(o["shipping_amount"]) == 0

print("--- 3. COD above threshold → shipping STILL charged ---")
st, co = req("POST", "/checkout", {
    "items": [{"product_id": 1, "quantity": 2}],
    "shipping_address": "Mumbai 400001",
    "shipping_pincode": "400001",
    "payment_method": "cod",
}, c)
o = req("GET", f"/orders/{co['order_id']}", None, c)[1]
print(f"  cod: shipping={o['shipping_amount']}")
assert float(o["shipping_amount"]) > 0

print("--- 4. PUT /cart/items/{id} sets absolute quantity ---")
req("POST", "/cart/items", {"product_id": 1, "quantity": 2}, c)
cart = req("GET", "/cart", None, c)[1]
print(f"  add(qty=2): items[0].qty={cart['items'][0]['quantity']}")
assert cart["items"][0]["quantity"] == 2
req("PUT", "/cart/items/1", {"quantity": 5}, c)
cart = req("GET", "/cart", None, c)[1]
print(f"  PUT qty=5: items[0].qty={cart['items'][0]['quantity']} (set, not added)")
assert cart["items"][0]["quantity"] == 5
req("PUT", "/cart/items/1", {"quantity": 0}, c)
cart = req("GET", "/cart", None, c)[1]
print(f"  PUT qty=0: items={len(cart['items'])}")
assert len(cart["items"]) == 0

print("--- 5. compare_at_price surfaces on cart lines when set ---")
req("PATCH", "/products/1", {"compare_at_price": 399}, admin)
req("POST", "/cart/items", {"product_id": 1, "quantity": 1}, c)
cart = req("GET", "/cart", None, c)[1]
print(f"  unit_price={cart['items'][0]['unit_price']} compare_at_price={cart['items'][0]['compare_at_price']}")
assert float(cart["items"][0]["compare_at_price"]) == 399
req("PATCH", "/products/1", {"compare_at_price": None}, admin)

# Cleanup
req("PATCH", "/settings", {"updates": {
    "shipping.free_threshold": "999",
    "shipping.provider": "none",
    "payments.instruments.upi.discount_percent": "5",
}}, admin)
print()
print("All tests passed.")
