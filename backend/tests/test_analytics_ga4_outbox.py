"""Server-side GA4 delivery: the outbox write, and the drain that pays it off.

What is actually under test is not "does an HTTP request get made" — it is the
small set of properties that decide whether GA4's revenue figure can be trusted:

- the row commits **with** the order, so an event can never describe a payment
  that was rolled back;
- one row per order however many times the paid path fires, because duplicate
  revenue in GA4 inflates ROAS and changes what the business spends money on;
- one rule for `transaction_id`, shared with the browser, or GA4 dedup silently
  fails;
- a withheld purchase is *recorded* as withheld rather than missing, because a
  missing row and a lost row look identical afterwards;
- `delivered` is written only after a send succeeded, never in advance;
- a conversion that will never arrive raises an alert instead of evaporating;
- PII is refused at the boundary, before a socket is opened;
- the api_secret does not appear in a log line or an exception, including the
  ones httpx builds itself out of the full URL.

Strategy mirrors test_analytics_queue.py / test_analytics_revenue_recognition.py:
no shared DB fixture, each test owns a `SessionLocal()`, every row it created is
deleted in a `finally` through a fresh session. Outbox rows are keyed by a
`GA4TEST` transaction-id prefix so a run that died mid-test cannot leave rows
that perturb the next one — each drain test wipes the prefix before it starts.

**No test here reaches Google.** The Measurement Protocol client is exercised
through `httpx.MockTransport`, and the outbox through a recording sender.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core import config as _config
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import (
    AlertRuleKey,
    AnalyticsAlert,
    AnalyticsEventOutbox,
    ConsentState,
    OutboxEventName,
    OutboxStatus,
)
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.user import User
from app.services.analytics import ga4, outbox
from app.services.analytics.ga4 import (
    Ga4Config,
    Ga4ContractError,
    Ga4Result,
    Ga4TransportError,
)
from app.services.analytics.tracking_events import Ev, PiiLeak, transaction_id_for

# ---------------------------------------------------------------------------
# Fixtures — local, not shared
# ---------------------------------------------------------------------------

#: Every outbox row these tests create carries this in its transaction id, so
#: teardown can delete exactly this module's rows and nothing else.
PREFIX = "GA4TEST"

#: A syntactically valid measurement id that belongs to no real property.
TEST_MEASUREMENT_ID = "G-TESTONLY123"

#: Distinctive enough that a substring check for it cannot pass by accident.
TEST_API_SECRET = "mp-api-secret-DO-NOT-LEAK-9f3a2b"

TEST_CONFIG = Ga4Config(
    measurement_id=TEST_MEASUREMENT_ID, api_secret=TEST_API_SECRET
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _order_number() -> str:
    return f"{PREFIX}-{_uid()}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _Owned:
    """Ids this test created, so teardown removes exactly them."""

    def __init__(self) -> None:
        self.users: list[int] = []
        self.products: list[int] = []
        self.orders: list[int] = []
        #: Outbox transaction ids that do NOT carry the prefix — the `ORD<id>`
        #: fallback case, which by definition cannot.
        self.transaction_ids: list[str] = []


def _create_user(db: Session, owned: _Owned) -> User:
    user = User(
        email=f"ga4outbox-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    owned.users.append(user.id)
    return user


def _create_product(db: Session, owned: _Owned, *, price: str = "500.00") -> Product:
    product = Product(
        sku=f"SKU-GA4-{_uid()}",
        name=f"GA4OutboxProduct {_uid()}",
        price=Decimal(price),
        cost=Decimal(price) / 2,
        stock=100,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)
    return product


def _create_order(
    db: Session,
    owned: _Owned,
    user: User,
    items: list[tuple[Product, int]],
    *,
    order_number: str | None = None,
    tax: str = "0",
    shipping: str = "0",
) -> Order:
    lines: list[OrderItem] = []
    gross = Decimal("0.00")
    for product, qty in items:
        lines.append(
            OrderItem(
                product_id=product.id,
                quantity=qty,
                unit_price=product.price,
                unit_cost=product.cost,
            )
        )
        gross += product.price * qty
    total = gross + Decimal(tax) + Decimal(shipping)

    order = Order(
        order_number=order_number,
        user_id=user.id,
        status=OrderStatus.PAID,
        subtotal=gross,
        tax_amount=Decimal(tax),
        shipping_amount=Decimal(shipping),
        total_amount=total,
        currency="INR",
        payment_method="prepaid",
        paid_at=datetime.now(timezone.utc),
    )
    order.items = lines
    db.add(order)
    db.flush()
    owned.orders.append(order.id)
    return order


def _wipe(extra_transaction_ids: list[str] | None = None) -> None:
    """Remove every outbox row and alert this module could have written."""
    with SessionLocal() as s:
        s.execute(
            text(
                "DELETE FROM analytics_event_outbox "
                "WHERE transaction_id LIKE :like"
            ),
            {"like": f"{PREFIX}%"},
        )
        s.execute(
            text(
                "DELETE FROM analytics_alerts "
                "WHERE metric = :metric AND dimension_value LIKE :like"
            ),
            {"metric": outbox.ALERT_METRIC, "like": f"{PREFIX}%"},
        )
        for txn in extra_transaction_ids or []:
            s.execute(
                text("DELETE FROM analytics_event_outbox WHERE transaction_id = :t"),
                {"t": txn},
            )
            s.execute(
                text(
                    "DELETE FROM analytics_alerts "
                    "WHERE metric = :metric AND dimension_value = :t"
                ),
                {"metric": outbox.ALERT_METRIC, "t": txn},
            )
        s.commit()


def _cleanup(owned: _Owned) -> None:
    """Delete exactly what this test created, through a fresh session."""
    _wipe(owned.transaction_ids)
    with SessionLocal() as s:
        if owned.orders:
            s.execute(
                text("DELETE FROM analytics_event_outbox WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM order_items WHERE order_id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
            s.execute(
                text("DELETE FROM orders WHERE id IN :ids"),
                {"ids": tuple(owned.orders)},
            )
        if owned.products:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(owned.products)},
            )
        if owned.users:
            s.execute(
                text("DELETE FROM users WHERE id IN :ids"),
                {"ids": tuple(owned.users)},
            )
        s.commit()


def _row(db: Session, transaction_id: str) -> AnalyticsEventOutbox | None:
    return db.execute(
        select(AnalyticsEventOutbox).where(
            AnalyticsEventOutbox.transaction_id == transaction_id
        )
    ).scalars().first()


def _insert_pending(
    db: Session,
    *,
    transaction_id: str,
    occurred_at: datetime,
    payload: dict[str, Any] | None = None,
    status: str = OutboxStatus.PENDING,
) -> AnalyticsEventOutbox:
    """A row written straight to the table, bypassing the order fixtures.

    Used by the drain tests, which care about statuses and batch shape rather
    than about how the payload was built.
    """
    row = AnalyticsEventOutbox(
        event_name=OutboxEventName.PURCHASE,
        transaction_id=transaction_id,
        order_id=None,
        occurred_at=occurred_at,
        payload=payload
        or {
            "transaction_id": transaction_id,
            "value": 100.0,
            "currency": "INR",
            "items": [{"item_id": "SKU1", "item_name": "Thing", "quantity": 1}],
        },
        consent_state=ConsentState.GRANTED,
        status=status,
        attempts=0,
    )
    db.add(row)
    db.flush()
    return row


class _Recorder:
    """A stand-in for `ga4.send_events` that records instead of sending."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def __call__(
        self,
        measurement_id: str,
        api_secret: str,
        client_id: str,
        events: Any,
        **kwargs: Any,
    ) -> Ga4Result:
        self.calls.append(
            {
                "measurement_id": measurement_id,
                "api_secret": api_secret,
                "client_id": client_id,
                "events": events,
                "kwargs": kwargs,
            }
        )
        if self.error is not None:
            raise self.error
        return Ga4Result(status_code=204)


# ===========================================================================
# 1. The write path joins the caller's transaction
# ===========================================================================
def test_enqueue_joins_the_callers_transaction_and_dies_with_it() -> None:
    """A rolled-back order leaves no event behind.

    This is the whole reason the row is written inline instead of by a listener
    after commit. An event that outlived its transaction would report revenue
    that was never collected, and GA4 has no way to take it back.
    """
    owned = _Owned()
    try:
        with SessionLocal() as db:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            order = _create_order(
                db, owned, user, [(product, 2)], order_number=_order_number()
            )
            row = outbox.enqueue_purchase(
                db, order, consent_state=ConsentState.GRANTED
            )
            transaction_id = row.transaction_id
            # Visible inside the transaction...
            assert _row(db, transaction_id) is not None
            db.rollback()

        # ...and gone once the caller rolls back. No commit of our own.
        with SessionLocal() as s:
            assert _row(s, transaction_id) is None
    finally:
        _cleanup(owned)


# ===========================================================================
# 2. Exactly-once
# ===========================================================================
def test_enqueuing_the_same_order_twice_writes_one_row() -> None:
    """The UNIQUE key is the delivery guarantee, and it must not raise.

    Every caller that can fire twice — a replayed webhook, a reconciliation
    sweep, an admin re-marking an order paid — has to be able to call this
    without checking first. A second call is a no-op that hands back the same
    row, not an IntegrityError that would poison the transaction taking the
    customer's money.
    """
    owned = _Owned()
    try:
        with SessionLocal() as db:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            order = _create_order(
                db, owned, user, [(product, 1)], order_number=_order_number()
            )

            first = outbox.enqueue_purchase(
                db, order, consent_state=ConsentState.GRANTED
            )
            first_id = first.id
            second = outbox.enqueue_purchase(
                db, order, consent_state=ConsentState.GRANTED
            )

            assert second.id == first_id
            db.commit()
            transaction_id = order.order_number

        with SessionLocal() as s:
            count = s.execute(
                select(AnalyticsEventOutbox).where(
                    AnalyticsEventOutbox.transaction_id == transaction_id
                )
            ).scalars().all()
            assert len(count) == 1
            # The caller's transaction survived the duplicate.
            assert s.get(Order, owned.orders[0]) is not None
    finally:
        _cleanup(owned)


# ===========================================================================
# 3. One rule for transaction_id
# ===========================================================================
def test_null_order_number_falls_back_to_ord_id() -> None:
    """`orders.order_number` is nullable, and both sides must fall back alike.

    If the browser derives `ORD41` and the server derives something else, GA4
    records two purchases for one order and deduplication fails silently —
    inflating revenue in a way that looks like growth.
    """
    owned = _Owned()
    try:
        with SessionLocal() as db:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            order = _create_order(db, owned, user, [(product, 1)], order_number=None)
            assert order.order_number is None

            row = outbox.enqueue_purchase(
                db, order, consent_state=ConsentState.GRANTED
            )
            owned.transaction_ids.append(row.transaction_id)

            assert row.transaction_id == f"ORD{order.id}"
            assert row.transaction_id == transaction_id_for(None, order.id)
            # The payload carries the same id, so the stored event and the
            # dedup key can never disagree.
            assert row.payload["transaction_id"] == row.transaction_id
            db.commit()
    finally:
        _cleanup(owned)


# ===========================================================================
# 4. Consent denied: recorded, never sent
# ===========================================================================
def test_denied_consent_is_recorded_and_never_delivered() -> None:
    """Withheld is a state, not an absence.

    Skipping the row entirely would make "the customer declined analytics" and
    "we lost the event" indistinguishable afterwards, and the store's own
    reconciliation would be short an order it definitely took.
    """
    owned = _Owned()
    recorder = _Recorder()
    try:
        _wipe()
        with SessionLocal() as db:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            order = _create_order(
                db, owned, user, [(product, 1)], order_number=_order_number()
            )
            row = outbox.enqueue_purchase(
                db,
                order,
                consent_state=ConsentState.DENIED,
                client_id="1234567890.1234567890",
                session_id="1700000000",
            )
            transaction_id = row.transaction_id

            assert row.status == OutboxStatus.SUPPRESSED_NO_CONSENT
            # The GA cookie ids are not ours to keep without consent, and an
            # unsent row has no use for them.
            assert row.client_id is None
            assert row.session_id is None
            db.commit()

        with SessionLocal() as drain_db:
            result = outbox.drain(
                drain_db, sender=recorder, config=TEST_CONFIG, limit=10
            )

        assert recorder.calls == []
        assert result["sent"] == 0
        assert result["suppressed"] >= 1

        with SessionLocal() as s:
            after = _row(s, transaction_id)
            assert after.status == OutboxStatus.SUPPRESSED_NO_CONSENT
            assert after.delivered_at is None
            assert after.attempts == 0
    finally:
        _cleanup(owned)


# ===========================================================================
# 5. Delivered only after a successful send
# ===========================================================================
def test_successful_send_marks_delivered_with_a_timestamp() -> None:
    owned = _Owned()
    recorder = _Recorder()
    try:
        _wipe()
        with SessionLocal() as db:
            user = _create_user(db, owned)
            product = _create_product(db, owned)
            order = _create_order(
                db,
                owned,
                user,
                [(product, 2)],
                order_number=_order_number(),
                tax="45.00",
                shipping="50.00",
            )
            row = outbox.enqueue_purchase(
                db,
                order,
                consent_state=ConsentState.GRANTED,
                client_id="1234567890.1234567890",
                session_id="1700000000",
            )
            transaction_id = row.transaction_id
            expected_value = float(order.total_amount)
            db.commit()

        with SessionLocal() as drain_db:
            result = outbox.drain(
                drain_db, sender=recorder, config=TEST_CONFIG, limit=10
            )

        assert result["sent"] == 1
        assert result["failed"] == 0
        assert len(recorder.calls) == 1

        call = recorder.calls[0]
        assert call["measurement_id"] == TEST_MEASUREMENT_ID
        # The consented cookie id is what joins the server event to the browser
        # session that earned the campaign credit.
        assert call["client_id"] == "1234567890.1234567890"
        event = call["events"][0]
        assert event["name"] == Ev.PURCHASE
        assert event["params"]["transaction_id"] == transaction_id
        assert event["params"]["value"] == expected_value
        assert event["params"]["currency"] == "INR"
        assert event["params"]["items"][0]["quantity"] == 2
        assert event["params"]["session_id"] == "1700000000"
        # Backdated to when the purchase happened, not to when the worker ran.
        assert call["kwargs"]["timestamp_micros"] > 0

        with SessionLocal() as s:
            after = _row(s, transaction_id)
            assert after.status == OutboxStatus.DELIVERED
            assert after.delivered_at is not None
            assert after.attempts == 1
            assert after.last_error is None
    finally:
        _cleanup(owned)


# ===========================================================================
# 6. A failed send is a failure, not a delivery
# ===========================================================================
def test_failed_send_increments_attempts_and_stays_pending() -> None:
    """The debt survives the failure. Nothing is marked delivered."""
    owned = _Owned()
    recorder = _Recorder(error=Ga4TransportError("GA4 /mp/collect unreachable"))
    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            _insert_pending(
                db, transaction_id=transaction_id, occurred_at=_utcnow()
            )
            db.commit()

        with SessionLocal() as drain_db:
            result = outbox.drain(
                drain_db, sender=recorder, config=TEST_CONFIG, limit=10
            )

        assert len(recorder.calls) == 1
        assert result["sent"] == 0
        assert result["failed"] == 1

        with SessionLocal() as s:
            after = _row(s, transaction_id)
            assert after.status == OutboxStatus.PENDING
            assert after.attempts == 1
            assert after.delivered_at is None
            assert "unreachable" in (after.last_error or "")
    finally:
        _cleanup(owned)


def test_a_failed_row_is_not_retried_immediately_within_one_pass() -> None:
    """One pass spends one attempt per row, not the whole budget on one row.

    Without this the drain would re-claim the row it just failed — it is still
    `pending` and still the oldest — and spin on it until the time budget ran
    out, starving every other conversion behind it.
    """
    owned = _Owned()
    recorder = _Recorder(error=Ga4TransportError("boom"))
    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            _insert_pending(
                db, transaction_id=transaction_id, occurred_at=_utcnow()
            )
            db.commit()

        with SessionLocal() as drain_db:
            outbox.drain(drain_db, sender=recorder, config=TEST_CONFIG, limit=10)

        assert len(recorder.calls) == 1

        # A second pass immediately afterwards must respect the backoff: the row
        # now has one failed attempt and `created_at` seconds ago.
        recorder2 = _Recorder(error=Ga4TransportError("boom"))
        with SessionLocal() as drain_db:
            outbox.drain(drain_db, sender=recorder2, config=TEST_CONFIG, limit=10)
        assert recorder2.calls == []

        with SessionLocal() as s:
            assert _row(s, transaction_id).attempts == 1
    finally:
        _cleanup(owned)


# ===========================================================================
# 7. A stuck conversion becomes visible
# ===========================================================================
def test_repeated_failure_dead_letters_with_an_alert() -> None:
    """A conversion GA4 will never receive must not disappear quietly.

    Every other surface looks healthy when this happens: the order is fine,
    internal revenue is complete, the worker keeps ticking. The only symptom is
    a number in a third-party console that is slightly too low, which nobody
    investigates. The alert is what turns it into something an operator sees.
    """
    owned = _Owned()
    max_attempts = 3
    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            row = _insert_pending(
                db, transaction_id=transaction_id, occurred_at=_utcnow()
            )
            db.flush()
            # Age the row past every backoff tier so the retries can be driven
            # back to back instead of over the next hour.
            db.execute(
                text("UPDATE analytics_event_outbox SET created_at = :old WHERE id = :id"),
                {"old": _utcnow() - timedelta(hours=6), "id": row.id},
            )
            db.commit()

        for _ in range(max_attempts):
            recorder = _Recorder(error=Ga4TransportError("GA4 is down"))
            with SessionLocal() as drain_db:
                outbox.drain(
                    drain_db,
                    sender=recorder,
                    config=TEST_CONFIG,
                    limit=10,
                    max_attempts=max_attempts,
                )

        with SessionLocal() as s:
            after = _row(s, transaction_id)
            assert after.status == OutboxStatus.FAILED
            assert after.attempts == max_attempts
            assert after.delivered_at is None

            alerts = s.execute(
                select(AnalyticsAlert).where(
                    AnalyticsAlert.metric == outbox.ALERT_METRIC,
                    AnalyticsAlert.dimension_value == transaction_id,
                )
            ).scalars().all()
            assert len(alerts) == 1
            assert alerts[0].rule_key == AlertRuleKey.GA4_SYNC_FAILURE
            assert alerts[0].context["attempts"] == max_attempts

        # A dead-lettered row stops burning worker time.
        recorder = _Recorder()
        with SessionLocal() as drain_db:
            outbox.drain(
                drain_db, sender=recorder, config=TEST_CONFIG, limit=10,
                max_attempts=max_attempts,
            )
        assert recorder.calls == []
    finally:
        _cleanup(owned)


# ===========================================================================
# 8. PII never reaches the wire
# ===========================================================================
def test_pii_payload_raises_before_any_request_is_made() -> None:
    """The check is at the boundary, so it fires before a socket is opened."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    with pytest.raises(PiiLeak):
        ga4.send_events(
            TEST_MEASUREMENT_ID,
            TEST_API_SECRET,
            "1234567890.1234567890",
            [
                {
                    "name": Ev.PURCHASE,
                    "params": {
                        "transaction_id": "ORD1",
                        "value": 100.0,
                        "currency": "INR",
                        "items": [{"item_id": "SKU1", "item_name": "Thing"}],
                        # The offending field. `item_name` above is a documented
                        # exception and must still pass.
                        "customer_email": "someone@example.com",
                    },
                }
            ],
            transport=httpx.MockTransport(handler),
        )

    assert seen == []


def test_a_pii_row_in_the_outbox_dead_letters_without_being_sent() -> None:
    """End to end: a leaky payload is a permanent failure, not a retry.

    Retrying a payload that cannot become valid burns the attempt budget and
    delays the alert a human needs. It is also the one failure mode where
    retrying would keep re-attempting to transmit the PII.
    """
    owned = _Owned()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)

    def sender(*args: Any, **kwargs: Any) -> Ga4Result:
        return ga4.send_events(*args, transport=transport, **kwargs)

    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            _insert_pending(
                db,
                transaction_id=transaction_id,
                occurred_at=_utcnow(),
                payload={
                    "transaction_id": transaction_id,
                    "value": 100.0,
                    "currency": "INR",
                    "items": [{"item_id": "SKU1", "item_name": "Thing"}],
                    "customer_phone": "9876543210",
                },
            )
            db.commit()

        with SessionLocal() as drain_db:
            result = outbox.drain(
                drain_db, sender=sender, config=TEST_CONFIG, limit=10
            )

        assert seen == []
        assert result["sent"] == 0
        assert result["failed"] == 1

        with SessionLocal() as s:
            after = _row(s, transaction_id)
            # One attempt, not five: a permanent error is not retried.
            assert after.status == OutboxStatus.FAILED
            assert after.attempts == 1
            assert after.delivered_at is None
            assert "PiiLeak" in (after.last_error or "")
            alerts = s.execute(
                select(AnalyticsAlert).where(
                    AnalyticsAlert.metric == outbox.ALERT_METRIC,
                    AnalyticsAlert.dimension_value == transaction_id,
                )
            ).scalars().all()
            assert len(alerts) == 1
    finally:
        _cleanup(owned)


# ===========================================================================
# 9. Bounded batch, bounded clock
# ===========================================================================
def test_drain_respects_the_batch_limit_and_the_time_budget() -> None:
    """Neither bound is a tuning knob: they are what stops a GA4 outage from
    wedging the worker in a loop it cannot leave."""
    owned = _Owned()
    try:
        _wipe()
        transaction_ids = [f"{PREFIX}-{_uid()}" for _ in range(3)]
        with SessionLocal() as db:
            for offset, txn in enumerate(transaction_ids):
                _insert_pending(
                    db,
                    transaction_id=txn,
                    occurred_at=_utcnow() - timedelta(minutes=60 - offset),
                )
            db.commit()

        # Batch limit: one row per pass, oldest first.
        recorder = _Recorder()
        with SessionLocal() as drain_db:
            result = outbox.drain(
                drain_db, sender=recorder, config=TEST_CONFIG, limit=1
            )
        assert result["sent"] == 1
        assert result["attempted"] == 1
        assert len(recorder.calls) == 1
        assert (
            recorder.calls[0]["events"][0]["params"]["transaction_id"]
            == transaction_ids[0]
        )
        assert result["skipped"] >= 2

        # Time budget: exhausted before the first claim, so nothing is touched.
        recorder = _Recorder()
        with SessionLocal() as drain_db:
            result = outbox.drain(
                drain_db, sender=recorder, config=TEST_CONFIG, limit=50, budget_ms=0
            )
        assert recorder.calls == []
        assert result["attempted"] == 0
        assert result["sent"] == 0
        assert result["budget_exhausted"] is True

        with SessionLocal() as s:
            statuses = [_row(s, t).status for t in transaction_ids]
        assert statuses == [
            OutboxStatus.DELIVERED,
            OutboxStatus.PENDING,
            OutboxStatus.PENDING,
        ]
    finally:
        _cleanup(owned)


def test_events_past_the_backdating_window_are_not_sent() -> None:
    """GA4 would answer 204 and record nothing; sending it undated would put
    the revenue on the wrong day. Neither is delivery."""
    owned = _Owned()
    recorder = _Recorder()
    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            _insert_pending(
                db,
                transaction_id=transaction_id,
                occurred_at=_utcnow()
                - timedelta(hours=outbox.GA4_BACKDATE_LIMIT_HOURS + 6),
            )
            db.commit()

        with SessionLocal() as drain_db:
            result = outbox.drain(
                drain_db, sender=recorder, config=TEST_CONFIG, limit=10
            )

        assert recorder.calls == []
        assert result["failed"] == 1
        with SessionLocal() as s:
            after = _row(s, transaction_id)
            assert after.status == OutboxStatus.FAILED
            assert after.delivered_at is None
            assert "backdating" in (after.last_error or "")
    finally:
        _cleanup(owned)


# ===========================================================================
# 10. Not configured is not "delivered"
# ===========================================================================
def test_unconfigured_ga4_marks_nothing_delivered() -> None:
    """The flag being off is a reason the debt cannot be paid, not a reason to
    write it off."""
    owned = _Owned()
    original = _config.settings.ANALYTICS_TRACKING_ENABLED
    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            _insert_pending(
                db, transaction_id=transaction_id, occurred_at=_utcnow()
            )
            db.commit()

        # (a) the documented kill switch.
        _config.settings.ANALYTICS_TRACKING_ENABLED = False
        with SessionLocal() as drain_db:
            result = outbox.drain(drain_db, limit=10)
        assert result["configured"] is False
        assert result["reason"] == "tracking_disabled"
        assert result["sent"] == 0
        assert result["remaining"] >= 1

        # (b) flag on, but no credentials have been entered.
        _config.settings.ANALYTICS_TRACKING_ENABLED = True
        with SessionLocal() as drain_db:
            result = outbox.drain(drain_db, limit=10)
        assert result["configured"] is False
        assert result["reason"] is not None
        assert result["sent"] == 0

        with SessionLocal() as s:
            after = _row(s, transaction_id)
            assert after.status == OutboxStatus.PENDING
            assert after.attempts == 0
            assert after.delivered_at is None
    finally:
        _config.settings.ANALYTICS_TRACKING_ENABLED = original
        _cleanup(owned)


def test_validation_mode_stops_delivery_rather_than_faking_it() -> None:
    """`/debug/mp/collect` answers 200 and delivers nothing.

    Treating that as a delivery would be the worst outcome this module can
    produce — the conversion destroyed, the queue drained clean, and no record
    anywhere that anything was lost. Refusing to run leaves the rows recoverable
    and puts the reason in the tick log.
    """
    owned = _Owned()
    recorder = _Recorder()
    debug_config = Ga4Config(
        measurement_id=TEST_MEASUREMENT_ID,
        api_secret=TEST_API_SECRET,
        debug_endpoint=True,
    )
    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            _insert_pending(
                db, transaction_id=transaction_id, occurred_at=_utcnow()
            )
            db.commit()

        with SessionLocal() as drain_db:
            result = outbox.drain(drain_db, sender=recorder, config=debug_config)

        assert recorder.calls == []
        assert result["configured"] is False
        assert result["reason"] == "debug_endpoint_enabled"
        with SessionLocal() as s:
            after = _row(s, transaction_id)
            assert after.status == OutboxStatus.PENDING
            assert after.delivered_at is None
    finally:
        _cleanup(owned)


def test_worker_deliver_pending_reports_not_configured() -> None:
    """The worker's adapter must surface the same refusal, not zeroes that read
    like an idle queue."""
    from app.services.analytics.worker import deliver_pending

    original = _config.settings.ANALYTICS_TRACKING_ENABLED
    try:
        _config.settings.ANALYTICS_TRACKING_ENABLED = False
        with SessionLocal() as db:
            result = deliver_pending(db, limit=5)
        assert result["configured"] is False
        assert result["reason"] == "tracking_disabled"
        assert result["delivered"] == 0
        assert result["implemented"] is True
    finally:
        _config.settings.ANALYTICS_TRACKING_ENABLED = original


# ===========================================================================
# 11. The api_secret never escapes
# ===========================================================================
def test_api_secret_never_appears_in_logs_or_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The Measurement Protocol puts the credential in the query string, which
    is the worst place for it: httpx builds error messages out of the full URL.

    Every assertion here is about a string some *other* library composed.
    """
    caplog.set_level(logging.DEBUG)

    # (a) a transport error whose message embeds the full URL.
    def exploding(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    with pytest.raises(Ga4TransportError) as transport_error:
        ga4.send_events(
            TEST_MEASUREMENT_ID,
            TEST_API_SECRET,
            "1.2",
            [{"name": "page_view", "params": {"page_location": "/x"}}],
            transport=httpx.MockTransport(exploding),
        )
    message = str(transport_error.value)
    assert TEST_API_SECRET not in message
    assert "api_secret=***" in message
    # No chained cause: a traceback would print the original message verbatim.
    assert transport_error.value.__cause__ is None

    # (b) an upstream error body that echoes the credential back at us.
    def echoing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"upstream failure for {request.url}")

    with pytest.raises(Ga4TransportError) as server_error:
        ga4.send_events(
            TEST_MEASUREMENT_ID,
            TEST_API_SECRET,
            "1.2",
            [{"name": "page_view", "params": {"page_location": "/x"}}],
            transport=httpx.MockTransport(echoing),
        )
    assert TEST_API_SECRET not in str(server_error.value)

    # (c) the success path, which is where the leak actually lives: httpx logs
    # `HTTP Request: POST <full url> "200 OK"` at INFO on *every* request, and
    # the Measurement Protocol puts the credential in that url.
    ga4.send_events(
        TEST_MEASUREMENT_ID,
        TEST_API_SECRET,
        "1.2",
        [{"name": "page_view", "params": {"page_location": "/x"}}],
        transport=httpx.MockTransport(lambda request: httpx.Response(204)),
    )
    assert any("google-analytics.com" in r.getMessage() for r in caplog.records), (
        "expected httpx to have logged the request — if it stopped, this test "
        "is no longer proving anything"
    )

    # (d) nothing logged along the way carries it.
    for record in caplog.records:
        assert TEST_API_SECRET not in record.getMessage()
        assert TEST_API_SECRET not in str(record.args or "")

    # (e) the config object itself is safe to interpolate into a log line.
    assert TEST_API_SECRET not in repr(TEST_CONFIG)
    assert TEST_API_SECRET not in str(TEST_CONFIG)


def test_the_stored_error_on_a_failed_row_is_redacted() -> None:
    """`last_error` is read by humans in the admin panel — it must not become a
    place the credential is durably written down."""
    owned = _Owned()
    leaky = Ga4TransportError(
        "GA4 /mp/collect unreachable: ConnectError: cannot connect to "
        f"https://www.google-analytics.com/mp/collect?api_secret={TEST_API_SECRET}"
    )
    recorder = _Recorder(error=leaky)
    try:
        _wipe()
        transaction_id = f"{PREFIX}-{_uid()}"
        with SessionLocal() as db:
            _insert_pending(
                db, transaction_id=transaction_id, occurred_at=_utcnow()
            )
            db.commit()

        with SessionLocal() as drain_db:
            outbox.drain(drain_db, sender=recorder, config=TEST_CONFIG, limit=10)

        with SessionLocal() as s:
            stored = _row(s, transaction_id).last_error or ""
        assert TEST_API_SECRET not in stored
        assert "***" in stored
    finally:
        _cleanup(owned)


def test_ga4_settings_are_not_readable_from_the_public_endpoint() -> None:
    """`/settings/public` is anonymous. The api_secret can WRITE events into the
    property, so it must never be on that allowlist.

    The measurement id is a different matter, and this test used to over-reach by
    banning it too. `G-XXXXXXXXXX` is a public identifier: it is visible in the
    page source the moment the tag loads, and the browser cannot load the tag
    without first reading it from us. Banning it would not have protected
    anything — it would simply have meant tracking never started, which is the
    silent-failure shape this phase already had to fix once.

    So the assertion is narrowed to the credentials, which is where the risk
    actually lives, and is driven by the SECRET set derived from the field
    schema rather than by a hand-listed set that would rot.
    """
    from app.api.v1.endpoints.settings import _PUBLIC_KEYS
    from app.services.analytics.integrations import SECRET_KEYS

    for key in SECRET_KEYS:
        assert key not in _PUBLIC_KEYS, f"{key} is a credential and must not be public"

    # The api_secret specifically — the one that can write to the property.
    assert ga4.SETTING_API_SECRET not in _PUBLIC_KEYS

    # And the converse, because it is the failure that actually bit: the browser
    # MUST be able to read the measurement id, or nothing ever loads.
    assert ga4.SETTING_MEASUREMENT_ID in _PUBLIC_KEYS, (
        "the measurement id must be publicly readable or the tag cannot load"
    )


# ===========================================================================
# The Measurement Protocol client itself
# ===========================================================================
def test_live_send_posts_the_expected_request_and_reads_204_honestly() -> None:
    """204 means "accepted for delivery". It is not a validation result, and
    `valid` says so by returning None rather than True."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(204)

    result = ga4.send_events(
        TEST_MEASUREMENT_ID,
        TEST_API_SECRET,
        "1234567890.1234567890",
        [
            {
                "name": Ev.PURCHASE,
                "params": {
                    "transaction_id": "ORD7",
                    "value": 100.0,
                    "currency": "INR",
                    "items": [{"item_id": "SKU1", "item_name": "Thing"}],
                },
            }
        ],
        timestamp_micros=1_700_000_000_000_000,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 204
    assert result.accepted is True
    assert result.valid is None

    request = captured[0]
    assert str(request.url).startswith(ga4.COLLECT_URL)
    assert request.url.params["measurement_id"] == TEST_MEASUREMENT_ID
    assert request.url.params["api_secret"] == TEST_API_SECRET
    body = request.read().decode()
    assert '"client_id"' in body
    assert '"timestamp_micros"' in body


def test_debug_send_uses_the_validation_endpoint_and_surfaces_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(ga4.DEBUG_COLLECT_URL)
        return httpx.Response(
            200,
            json={
                "validationMessages": [
                    {"fieldPath": "events", "description": "bad event"}
                ]
            },
        )

    result = ga4.send_events(
        TEST_MEASUREMENT_ID,
        TEST_API_SECRET,
        "1.2",
        [{"name": "page_view", "params": {"page_location": "/x"}}],
        debug=True,
        transport=httpx.MockTransport(handler),
    )

    assert result.status_code == 200
    assert result.valid is False
    assert result.validation_messages[0]["description"] == "bad event"


def test_missing_required_parameters_are_refused_before_sending() -> None:
    """A purchase with no `transaction_id` cannot be deduplicated against the
    browser's, which is the single failure that inflates reported revenue.
    Present-but-empty counts as missing."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    with pytest.raises(Ga4ContractError):
        ga4.send_events(
            TEST_MEASUREMENT_ID,
            TEST_API_SECRET,
            "1.2",
            [
                {
                    "name": Ev.PURCHASE,
                    "params": {
                        "transaction_id": "ORD9",
                        "value": 10.0,
                        "currency": "INR",
                        "items": [],  # present, and useless
                    },
                }
            ],
            transport=httpx.MockTransport(handler),
        )
    assert seen == []


def test_client_id_fallback_is_stable_for_one_transaction() -> None:
    """A fresh id per attempt would turn one retried purchase into several
    users the moment a send finally succeeded."""
    first = outbox.client_id_for("ORD42")
    assert first == outbox.client_id_for("ORD42")
    assert first != outbox.client_id_for("ORD43")
    assert "." in first
