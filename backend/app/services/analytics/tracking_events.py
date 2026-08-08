"""The versioned tracking event contract — shared by the browser and the server.

Both sides emit `purchase`. If they disagree about the event name, the parameter
shape or the transaction id, the Data Reconciliation view compares two things
that were never comparable and reports a discrepancy that isn't real. So the
contract lives here, in one place, and is dumped to JSON for the frontend the
same way the analytics registry is.

Three rules this module exists to enforce
=========================================

**1. No PII leaves the building.** GA4's terms forbid it and Clarity's too, but
more practically: an email address in an event parameter is permanent, arrives
in a third party's logs, and cannot be recalled. `PII_DENYLIST` is checked at
the moment of delivery, not at review time — see `assert_no_pii`.

**2. `transaction_id` is the internal order number, and there is exactly one
rule for deriving it.** `orders.order_number` is NULLABLE (allocated after the
flush that assigns the id, so it can embed that id), and callers fall back to
``f"ORD{id}"``. If the browser and the server derive it differently, GA4 sees
two purchases for one order and dedup fails silently. `transaction_id_for` is
that single rule.

**3. A success-page URL is not proof of purchase.** Only a backend-confirmed
order status is. The browser may report a purchase for attribution, but the
authoritative event is the server's, written inside the order-paid transaction.
"""
from __future__ import annotations

from typing import Any

#: Bumped when the shape of an event's parameters changes in a way a consumer
#: could notice. Sent as `event_schema_version` so a GA4 property receiving two
#: versions during a rollout can tell them apart.
SCHEMA_VERSION = 1


class Ev:
    """Standard GA4 e-commerce events. Names are GA4's, not ours — renaming one
    would silently opt the property out of GA4's built-in reporting."""

    PAGE_VIEW = "page_view"
    VIEW_ITEM_LIST = "view_item_list"
    SELECT_ITEM = "select_item"
    VIEW_ITEM = "view_item"
    ADD_TO_WISHLIST = "add_to_wishlist"
    ADD_TO_CART = "add_to_cart"
    REMOVE_FROM_CART = "remove_from_cart"
    VIEW_CART = "view_cart"
    BEGIN_CHECKOUT = "begin_checkout"
    ADD_SHIPPING_INFO = "add_shipping_info"
    ADD_PAYMENT_INFO = "add_payment_info"
    PURCHASE = "purchase"
    REFUND = "refund"
    VIEW_PROMOTION = "view_promotion"
    SELECT_PROMOTION = "select_promotion"
    SEARCH = "search"
    LOGIN = "login"
    SIGN_UP = "sign_up"


class BizEv:
    """Business events with no GA4 equivalent. Prefixed nowhere — GA4 custom
    event names must not collide with reserved ones, and none of these do."""

    PAYMENT_ATTEMPT = "payment_attempt"
    PAYMENT_FAILED = "payment_failed"
    COUPON_APPLIED = "coupon_applied"
    COUPON_FAILED = "coupon_failed"
    SEARCH_NO_RESULTS = "search_no_results"
    ORDER_CANCELLED = "order_cancelled"
    REVIEW_SUBMITTED = "review_submitted"
    SUPPORT_STARTED = "support_started"


#: Item-level fields, GA4's canonical names. `item_id` is the SKU — not the
#: numeric product id — because a SKU is what a merchandiser recognises in a
#: GA4 report, and it survives a database migration.
ITEM_FIELDS = (
    "item_id",
    "item_name",
    "item_brand",
    "item_category",
    "item_category2",
    "item_category3",
    "item_variant",
    "item_list_id",
    "item_list_name",
    "index",
    "price",
    "quantity",
    "discount",
    "coupon",
)


#: Parameters that must be present for an event to be worth sending at all.
REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    Ev.PURCHASE: ("transaction_id", "value", "currency", "items"),
    Ev.REFUND: ("transaction_id", "currency"),
    Ev.ADD_TO_CART: ("currency", "value", "items"),
    Ev.BEGIN_CHECKOUT: ("currency", "value", "items"),
    Ev.VIEW_ITEM: ("currency", "value", "items"),
    Ev.SEARCH: ("search_term",),
}


#: Parameter names that must NEVER appear in an outbound event, at any depth.
#:
#: Checked by substring, deliberately: `customer_email`, `billing_email` and
#: `email_address` are all caught by "email". Over-blocking a harmless field is
#: recoverable; leaking one is not.
PII_DENYLIST = (
    "email",
    "phone",
    "mobile",
    "name",          # deliberately broad — see PII_ALLOWED_EXACT for the escapes
    "address",
    "street",
    "pincode",
    "postcode",
    "zip",
    "password",
    "otp",
    "card",
    "cvv",
    "upi",
    "gstin",
    "pan",
    "dob",
    "birth",
)

#: GA4's own vocabulary collides with the denylist. These exact keys are the
#: documented exceptions, and they are matched exactly rather than by substring
#: so `item_name` passes while `customer_name` does not.
PII_ALLOWED_EXACT = frozenset(
    {
        "item_name",
        "item_list_name",
        "promotion_name",
        "creative_name",
        "currency",  # contains no denylisted substring, listed for clarity
    }
)


class PiiLeak(ValueError):
    """Raised when an outbound payload contains a denylisted parameter.

    Deliberately fatal rather than a warning that strips the field: a payload
    carrying PII means the *caller* is wrong, and silently sanitising it would
    let the bug ship and recur everywhere else that caller is copied.
    """


def _offending_keys(payload: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{path}.{key}" if path else str(key)
            lowered = str(key).lower()
            if lowered not in PII_ALLOWED_EXACT and any(
                bad in lowered for bad in PII_DENYLIST
            ):
                found.append(here)
            found.extend(_offending_keys(value, here))
    elif isinstance(payload, (list, tuple)):
        for i, value in enumerate(payload):
            found.extend(_offending_keys(value, f"{path}[{i}]"))
    return found


def assert_no_pii(payload: dict[str, Any]) -> None:
    """Raise if an outbound event carries a denylisted parameter.

    Called at the delivery boundary, so it catches a leak introduced by any
    caller rather than only the ones someone remembered to review.
    """
    offenders = _offending_keys(payload)
    if offenders:
        raise PiiLeak(
            "refusing to send an analytics event containing PII-shaped "
            f"parameters: {sorted(offenders)}. Remove them at the source — this "
            "is not sanitised automatically, because a silently stripped field "
            "hides the bug instead of fixing it."
        )


def transaction_id_for(order_number: str | None, order_id: int) -> str:
    """The ONE rule for deriving a GA4 transaction id from an order.

    `orders.order_number` is nullable by design (it is allocated after the flush
    that assigns the id, so it can embed it), and the rest of the codebase falls
    back to ``f"ORD{id}"``. Browser and server must use this same function, or
    GA4 receives two ids for one order and deduplication silently fails —
    inflating revenue in a way that looks like growth.
    """
    return order_number or f"ORD{order_id}"


def to_json_dict() -> dict[str, Any]:
    """Serialised contract, dumped for the frontend so JS cannot drift."""
    return {
        "schema_version": SCHEMA_VERSION,
        "events": sorted(
            v for k, v in vars(Ev).items() if not k.startswith("_") and isinstance(v, str)
        ),
        "business_events": sorted(
            v for k, v in vars(BizEv).items() if not k.startswith("_") and isinstance(v, str)
        ),
        "item_fields": list(ITEM_FIELDS),
        "required_params": {k: list(v) for k, v in sorted(REQUIRED_PARAMS.items())},
        "pii_denylist": list(PII_DENYLIST),
        "pii_allowed_exact": sorted(PII_ALLOWED_EXACT),
    }
