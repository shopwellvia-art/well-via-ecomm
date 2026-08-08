"""The settlement surface: upload API, view 64, and the ESTIMATED->ACTUAL wire.

``tests/test_analytics_settlements.py`` proves the ingest/matching *service*.
This module proves the three things that were built on top of it:

1. **The upload endpoint** — permissioned, audited, idempotent, and atomic
   through HTTP: a malformed file 422s with every problem named and the table
   is byte-identical afterwards. ``rows_new == 0`` on a re-upload is asserted
   against a full row snapshot, not a count — a count survives a doubled fee.
2. **View 64** — PARTIAL is the ceiling; the runtime probe returns the
   ``not_configured`` refusal (empty ``sources``, NOT_CONFIGURED warning naming
   ``gateway_settlement_report``) when no report exists, and after an upload
   the view resolves end-to-end with the two populations kept apart: fees on
   the payment day, cash on the settlement day, never summed.
3. **Margin precedence** — the single highest-stakes assertion here is the
   no-op: with ZERO settlement rows, ``MarginService.compute`` must be
   byte-identical to a run with the observed-fee path stubbed out, because
   every pre-existing margin test implicitly assumes exactly that. Then the
   wire itself: a fully covered day replaces the 2% estimate with the observed
   fee+tax at ACTUAL; a partially covered day blends and is NOT ACTUAL.

Permission isolation follows the cost-admin precedent to the letter: every user
is NON-ADMIN (``User.has_permission`` short-circuits for admins, so an admin
proves nothing), and the permission lookup itself is asserted before any status
code, so a 403 cannot pass because the grants were never written.

Isolation strategy
------------------
The whole fixture lives in **1977** — 1974-1976, 1990, 1996-1999, 2001-2016,
2018, 2019, 2021, 2024 and 2026 are taken by other suites, and the live demo
PERF- rows live in 2026-05..07 and are never touched. Each test class owns a
different month. ``_sweep()`` runs before and after every test against the
whole year, by date range and name pattern rather than by remembered ids, so
the suite survives a run that died mid-test — and it clears 1977 recompute
queue rows on both sides, because ``drain_queue`` claims across ALL rows and a
leaked row would be claimed by another suite's worker.

House style: no conftest DB fixture; each test owns its ``SessionLocal()`` and
tears down in a ``finally`` through a fresh session.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_settlements_api.py -q
"""
from __future__ import annotations

import csv
import io
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.v1.endpoints import analytics_settlements, analytics_views
from app.core import config as _config
from app.core.exceptions import register_exception_handlers
from app.core.security import create_access_token, hash_password
from app.db.session import SessionLocal
from app.models.analytics_control import AnalyticsCostRule, CostQuality, CostScope, CostType, CostUnit
from app.models.analytics_settlement import PaymentSettlement, SettlementMatchKey
from app.models.audit import AuditEvent
from app.models.order import Order, OrderItem, OrderStatus
from app.models.order_payment import OrderPayment, PaymentTxnStatus
from app.models.product import Product
from app.models.user import User
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.cost_rules import CostRuleResolver
from app.services.analytics.margin import MarginService
from app.services.analytics.registry import get_view
from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS, EXTERNAL_CAPABILITIES
from app.services.analytics.settlements import AmountUnit, ingest_settlement_csv
from app.services.analytics.types import Capability, MetricQuality, ResolverId, ViewState
from app.services.analytics.view_service import view_module_slug

API_PREFIX = "/api/v1/analytics"

WRITE_PERM = analytics_settlements.WRITE_PERMISSION   # analytics.budgets.manage
FINANCE_PERM = analytics_settlements.READ_PERMISSION  # analytics.finance.view
BASE_PERM = "analytics.view"
#: A real, seeded, scoped analytics permission that is NOT finance. Held by the
#: user that must be 403, so the 403 proves tiering rather than an empty role.
OTHER_ANALYTICS_PERM = "analytics.orders.view"

GATEWAY = "razorpay"
JOB = "settlement_daily"
WORKER_ID = "settlement-api-tests"
RULE_SOURCE = "sapi-test"

# ---------------------------------------------------------------------------
# The 1977 sandbox
# ---------------------------------------------------------------------------
SANDBOX_FROM = date(1977, 1, 1)
SANDBOX_TO = date(1978, 1, 1)

USER_EMAIL_PATTERN = "sapi-%@example.com"
SKU_PATTERN = "SKU-SAPI-%"


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _mtid() -> str:
    """The exact shape ``payment_service`` mints: ``ORD`` + 24 uppercase hex."""
    return f"ORD{uuid.uuid4().hex[:24].upper()}"


def _d(value: str) -> Decimal:
    return Decimal(value)


# ---------------------------------------------------------------------------
# App / client / users
# ---------------------------------------------------------------------------
@pytest.fixture()
def app() -> FastAPI:
    """A local app: ANALYTICS_V2_ENABLED is false by default, so the real app
    never mounts these routers. Exception handlers are registered so an
    AppError is a status code rather than an unhandled 500."""
    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(analytics_settlements.router, prefix=API_PREFIX)
    test_app.include_router(analytics_views.router, prefix=API_PREFIX)
    return test_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """The upload limit is 10 per 5 minutes per IP and every test here shares
    one IP; a filling bucket would fail the third consecutive run."""
    original = _config.settings.RATE_LIMIT_ENABLED
    _config.settings.RATE_LIMIT_ENABLED = False
    try:
        yield
    finally:
        _config.settings.RATE_LIMIT_ENABLED = original


class PermissionedUser:
    """A throwaway NON-ADMIN user holding exactly the named permissions.

    Non-admin is load-bearing: ``User.has_permission`` returns True for every
    permission when ``is_admin`` is set, so an admin account would satisfy
    ``require_permission`` without a single grant being written or read.
    """

    def __init__(self, *permissions: str) -> None:
        from app.models.rbac import Permission, Role

        self.role_ids: list[int] = []
        self.created_permission_ids: list[int] = []

        with SessionLocal() as db:
            user = User(
                email=f"sapi-{_uid()}@example.com",
                hashed_password=hash_password("TestPass123!"),
                is_active=True,
                is_admin=False,
            )
            db.add(user)
            db.flush()

            if permissions:
                role = Role(
                    name=f"sapi-test-{_uid()}", description="test role", is_system=False
                )
                db.add(role)
                db.flush()
                for name in permissions:
                    perm = db.query(Permission).filter(Permission.name == name).first()
                    if perm is None:
                        perm = Permission(
                            name=name, description="test-created", group_name="Analytics"
                        )
                        db.add(perm)
                        db.flush()
                        self.created_permission_ids.append(perm.id)
                    role.permissions.append(perm)
                db.flush()
                self.role_ids.append(role.id)
                user.roles.append(role)

            db.commit()
            self.id = user.id
            self.email = user.email
        self.token = create_access_token(self.id)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def holds(self) -> dict[str, bool]:
        """What the RBAC layer ACTUALLY believes, freshly read. Asserting this
        before a status code is what separates 'correctly denied' from 'the
        grants were never written'."""
        with SessionLocal() as db:
            user = db.get(User, self.id)
            assert user is not None
            return {
                name: user.has_permission(name)
                for name in (BASE_PERM, FINANCE_PERM, WRITE_PERM, OTHER_ANALYTICS_PERM)
            }

    def cleanup(self) -> None:
        with SessionLocal() as s:
            s.execute(
                text("DELETE FROM audit_events WHERE actor_user_id = :uid"),
                {"uid": self.id},
            )
            s.execute(text("DELETE FROM user_roles WHERE user_id = :uid"), {"uid": self.id})
            for role_id in self.role_ids:
                s.execute(
                    text("DELETE FROM role_permissions WHERE role_id = :rid"),
                    {"rid": role_id},
                )
                s.execute(text("DELETE FROM roles WHERE id = :rid"), {"rid": role_id})
            for perm_id in self.created_permission_ids:
                s.execute(text("DELETE FROM permissions WHERE id = :pid"), {"pid": perm_id})
            s.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": self.id})
            s.commit()


@pytest.fixture()
def make_user():
    created: list[PermissionedUser] = []

    def _make(*permissions: str) -> PermissionedUser:
        user = PermissionedUser(*permissions)
        created.append(user)
        return user

    try:
        yield _make
    finally:
        for user in created:
            user.cleanup()


# ---------------------------------------------------------------------------
# Fixture builders (orders, CSVs) — same shapes the service tests use
# ---------------------------------------------------------------------------
def _create_product(db: Session) -> Product:
    product = Product(
        sku=f"SKU-SAPI-{_uid()}",
        name=f"SettlementApiTestProduct {_uid()}",
        price=_d("1000.00"),
        cost=_d("400.00"),
        stock=1000,
    )
    db.add(product)
    db.flush()
    return product


def _create_paid_order(
    db: Session,
    owner: User | int,
    product: Product,
    *,
    at: datetime,
    total: str = "1000.00",
) -> tuple[Order, str]:
    """A prepaid PAID order plus its gateway leg. Returns (order, mtid)."""
    mtid = _mtid()
    user_id = owner if isinstance(owner, int) else owner.id
    order = Order(
        order_number=f"WV-SAPI-{_uid()}",
        user_id=user_id,
        status=OrderStatus.PAID,
        subtotal=_d(total),
        tax_amount=_d("0"),
        discount_amount=_d("0"),
        payment_discount_amount=_d("0"),
        shipping_amount=_d("0"),
        cod_surcharge_amount=_d("0"),
        cod_balance=_d("0.00"),
        total_amount=_d(total),
        currency="INR",
        payment_method="prepaid",
        payment_intent_id=mtid,
        gateway_code=GATEWAY,
        created_at=at,
        paid_at=at,
    )
    order.items = [
        OrderItem(
            product_id=product.id,
            quantity=1,
            unit_price=_d(total),
            unit_cost=product.cost,
        )
    ]
    db.add(order)
    db.flush()
    db.add(
        OrderPayment(
            order_id=order.id,
            gateway=GATEWAY,
            payment_method="prepaid",
            payment_status=PaymentTxnStatus.PAID,
            amount=_d(total),
            currency="INR",
            transaction_reference=mtid,
            created_at=at,
            paid_at=at,
        )
    )
    db.flush()
    return order, mtid


_HEADER = (
    "entity_id,type,debit,credit,amount,currency,fee,tax,on_hold,settled,"
    "created_at,settled_at,settlement_id,settlement_utr,payment_id,order_id,"
    "order_receipt,method,card_network"
)


def _line(
    entity_id: str,
    *,
    kind: str = "payment",
    credit: str = "0",
    debit: str = "0",
    fee: str = "0",
    tax: str = "0",
    created_at: str,
    settled_at: str = "",
    settlement_id: str = "",
    receipt: str = "",
) -> str:
    amount = credit if credit not in ("0", "") else debit
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(
        [
            entity_id, kind, debit, credit, amount, "INR", fee, tax,
            "No" if settlement_id else "Yes",
            "Yes" if settlement_id else "No",
            created_at, settled_at, settlement_id, "",
            entity_id if kind == "payment" else "", "",
            receipt, "upi", "",
        ]
    )
    return buffer.getvalue()


def _csv(*lines: str) -> str:
    return "\n".join((_HEADER, *lines)) + "\n"


def _upload(client: TestClient, user: PermissionedUser, name: str, content: str, **data):
    payload = {"amount_unit": AmountUnit.MAJOR, **data}
    return client.post(
        f"{API_PREFIX}/admin/settlements/upload",
        files={"file": (name, content.encode("utf-8"), "text/csv")},
        data=payload,
        headers=user.headers,
    )


def _view_url() -> str:
    view = get_view_64()
    return f"{API_PREFIX}/modules/{view_module_slug(view)}/views/{view.slug}"


def get_view_64():
    from app.services.analytics import registry

    view = next(v for m in registry.MODULES for v in m.views if v.number == 64)
    return view


def _get_view(client: TestClient, user: PermissionedUser, date_from: date, date_to: date, **extra):
    params = {
        "period": "custom",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "comparison": "none",
        **extra,
    }
    return client.get(_view_url(), params=params, headers=user.headers)


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------
def _sweep_once() -> None:
    """Remove every row this module could have written, anywhere in 1977.

    By date range and name pattern, never an in-memory id list, so a run that
    died before teardown is cleaned by the next one. ``payment_settlements`` is
    swept on BOTH date columns — a line transacted in December 1977 can settle
    in January 1978.
    """
    bounds: dict[str, Any] = {"a": SANDBOX_FROM, "b": SANDBOX_TO}
    with SessionLocal() as s:
        order_ids = [
            int(r[0])
            for r in s.execute(
                text("SELECT id FROM orders WHERE created_at >= :a AND created_at < :b"),
                bounds,
            ).all()
        ]
        user_ids = [
            int(r[0])
            for r in s.execute(
                text("SELECT id FROM users WHERE email LIKE :pat"),
                {"pat": USER_EMAIL_PATTERN},
            ).all()
        ]
        product_ids = [
            int(r[0])
            for r in s.execute(
                text("SELECT id FROM products WHERE sku LIKE :pat"),
                {"pat": SKU_PATTERN},
            ).all()
        ]

        s.execute(
            text(
                "DELETE FROM payment_settlements "
                "WHERE (payment_date >= :a AND payment_date < :b) "
                "   OR (settlement_date >= :a AND settlement_date < :b)"
            ),
            bounds,
        )
        s.execute(
            text(
                "DELETE FROM agg_settlement_daily "
                "WHERE bucket_date >= :a AND bucket_date < :b"
            ),
            bounds,
        )
        # Queue rows MUST go on both sides of every test: drain_queue claims by
        # (priority, bucket_date) across ALL rows, so a leaked 1977 row would be
        # claimed by another suite's runner and fail in a different file.
        s.execute(
            text(
                "DELETE FROM analytics_recompute_queue "
                "WHERE bucket_date >= :a AND bucket_date < :b"
            ),
            bounds,
        )
        s.execute(
            text("DELETE FROM analytics_cost_rules WHERE source = :src"),
            {"src": RULE_SOURCE},
        )
        s.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :w"),
            {"w": WORKER_ID},
        )
        if order_ids:
            ids = {"ids": tuple(order_ids)}
            for child in ("order_items", "order_payments", "shipments"):
                s.execute(text(f"DELETE FROM {child} WHERE order_id IN :ids"), ids)
            s.execute(text("DELETE FROM payment_events WHERE order_id IN :ids"), ids)
            s.execute(text("DELETE FROM orders WHERE id IN :ids"), ids)
        if user_ids:
            ids = {"ids": tuple(user_ids)}
            s.execute(text("DELETE FROM points_transactions WHERE user_id IN :ids"), ids)
            s.execute(text("DELETE FROM users WHERE id IN :ids"), ids)
        if product_ids:
            s.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(product_ids)},
            )
        s.commit()


def _sweep(attempts: int = 4) -> None:
    for attempt in range(attempts):
        try:
            _sweep_once()
            return
        except OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25 * (attempt + 1))


@pytest.fixture(autouse=True)
def _sandbox():
    _sweep()
    try:
        yield
    finally:
        _sweep()


def _snapshot(db: Session) -> list[tuple]:
    """Every field of every 1977 settlement row, for byte-level comparison."""
    rows = (
        db.execute(
            select(PaymentSettlement)
            .where(
                PaymentSettlement.payment_date >= SANDBOX_FROM,
                PaymentSettlement.payment_date < SANDBOX_TO,
            )
            .order_by(PaymentSettlement.gateway, PaymentSettlement.transaction_id)
        )
        .scalars()
        .all()
    )
    return [
        (
            r.gateway, r.transaction_id, r.transaction_type, r.settlement_id,
            r.settlement_utr, r.currency, r.method, r.gross_minor, r.fee_minor,
            r.tax_minor, r.net_minor, r.transacted_at, r.settled_at,
            r.payment_date, r.settlement_date, r.gateway_payment_id,
            r.gateway_order_id, r.gateway_reference, r.merchant_transaction_id,
            r.match_status, r.match_key, r.order_id, r.order_payment_id,
            r.source, r.source_file, r.source_row, r.created_at,
        )
        for r in rows
    ]


def _run_job(db: Session, day: date) -> None:
    AggregationRunner(db, worker_id=WORKER_ID).run_bucket(JOB, day)


def _gateway_rule(db: Session, pct: str = "2.00") -> AnalyticsCostRule:
    """A GLOBAL gateway-fee rule covering exactly 1977. Direct insert (this is
    not an endpoint test), tagged with `source` so the sweep owns it."""
    rule = AnalyticsCostRule(
        cost_type=CostType.GATEWAY_FEE,
        scope=CostScope.GLOBAL,
        scope_value="-",
        value=_d(pct),
        unit=CostUnit.PCT,
        currency="INR",
        quality=CostQuality.ESTIMATED,
        effective_from=SANDBOX_FROM,
        effective_to=date(1977, 12, 31),
        source=RULE_SOURCE,
        note="settlement API test rule",
    )
    db.add(rule)
    db.commit()
    return rule


class _FakeRedis:
    """Per-instance stand-in so the resolver's 60s cache cannot leak a
    pre-edit rate between two computations in one test."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value

    def mget(self, keys):
        return [self.store.get(k) for k in keys]

    def delete(self, key):
        self.store.pop(key, None)

    def pipeline(self):
        return _FakePipe(self)

    def scan_iter(self, match="*", count=100):
        prefix = match.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]


class _FakePipe:
    def __init__(self, parent):
        self.parent, self.ops = parent, []

    def setex(self, key, ttl, value):
        self.ops.append((key, ttl, value))
        return self

    def execute(self):
        for key, ttl, value in self.ops:
            self.parent.setex(key, ttl, value)
        self.ops.clear()


def _margin(db: Session) -> MarginService:
    return MarginService(db, cost_resolver=CostRuleResolver(db, redis_client=_FakeRedis()))


def _component(result, cost_type: str):
    return next(c for c in result.components if c.cost_type == cost_type)


# ===========================================================================
# 0. The registry decision, pinned
# ===========================================================================
class TestRegistryDecision:
    def test_view_64_is_partial_and_keyed_to_the_report_capability(self):
        view = get_view_64()
        assert view.state is ViewState.PARTIAL, (
            "View 64 must be PARTIAL — the ceiling for a CSV-fed view whose "
            "fees are ACTUAL only on covered days."
        )
        assert view.requires == (Capability.GATEWAY_SETTLEMENT_REPORT,), (
            "View 64 is keyed to the settlement REPORT capability (satisfied by "
            "an upload), not the unbuilt settlements API."
        )
        assert view.resolver is ResolverId.CUSTOM
        assert view.params.get("fn") in CUSTOM_FUNCTIONS
        assert view.export is True
        assert "future work" in view.limitation.lower(), (
            "The limitation must keep saying the API integration is future "
            "work — the capability re-key must not silently retire that fact."
        )

    def test_report_capability_is_not_classified_external(self):
        """`special.EXTERNAL_CAPABILITIES` short-circuits several resolvers into
        a permanent refusal. The report capability is satisfied internally (by
        an upload) and must never be sorted into that set — this is the one
        contract this workstream has with special.py's owner."""
        assert Capability.GATEWAY_SETTLEMENT_REPORT not in EXTERNAL_CAPABILITIES
        assert Capability.GATEWAY_SETTLEMENT_API in EXTERNAL_CAPABILITIES

    def test_module_lookup_matches_the_registry(self):
        view = get_view_64()
        module_slug = view_module_slug(view)
        assert module_slug, "view 64 must belong to a module"
        assert get_view(module_slug, view.slug) is view


# ===========================================================================
# 1. Upload: ingests, matches, audits; re-upload is a no-op   (March 1977)
# ===========================================================================
class TestUpload:
    def test_upload_ingests_matches_audits_and_reupload_is_noop(self, client, make_user):
        user = make_user(BASE_PERM, FINANCE_PERM, WRITE_PERM)
        assert user.holds()[WRITE_PERM] is True

        db = SessionLocal()
        try:
            product = _create_product(db)
            # Orders belong to a pattern-swept owner, NOT the API user: the
            # user fixture tears down before the autouse sweep, and deleting a
            # user who still owns 1977 orders trips the FK RESTRICT.
            owner = _owner(db)
            _, mtid_a = _create_paid_order(
                db, owner, product, at=datetime(1977, 3, 8, 10, 0)
            )
            _, mtid_b = _create_paid_order(
                db, owner, product, at=datetime(1977, 3, 9, 10, 0)
            )
            db.commit()

            name = f"razorpay-{_uid()}.csv"
            body = _csv(
                _line(f"pay_{_uid()}", credit="1000.00", fee="20.00", tax="3.60",
                      created_at="1977-03-08 15:00:00",
                      settled_at="1977-03-11 09:00:00",
                      settlement_id="setl_SAPI1", receipt=mtid_a),
                _line(f"pay_{_uid()}", credit="1000.00", fee="20.00", tax="3.60",
                      created_at="1977-03-09 15:00:00",
                      settled_at="1977-03-11 09:00:00",
                      settlement_id="setl_SAPI1", receipt=mtid_b),
                # A line the gateway paid us that we cannot explain. A finding.
                _line(f"pay_{_uid()}", credit="500.00", fee="10.00", tax="1.80",
                      created_at="1977-03-09 16:00:00",
                      settled_at="1977-03-11 09:00:00",
                      settlement_id="setl_SAPI1"),
            )

            response = _upload(client, user, name, body)
            assert response.status_code == 201, response.text
            data = response.json()
            assert data["rows_parsed"] == 3
            assert data["rows_new"] == 3
            assert data["matched"] == 2
            assert data["unmatched"] == 1
            assert data["ambiguous"] == 0
            assert "1977-03-08" in data["payment_dates"]
            assert "1977-03-09" in data["payment_dates"]
            assert data["settlement_dates"] == ["1977-03-11"]
            # gross 2500.00, fee 50.00, tax 9.00 — integer paise, signed.
            assert data["gross_minor"] == 250000
            assert data["fee_minor"] == 5000
            assert data["tax_minor"] == 900
            assert data["net_minor"] == 250000 - 5000 - 900
            assert data["recompute_jobs"] == [JOB]
            assert data["recompute_queued"] == 3  # 03-08, 03-09, 03-11
            assert data["warnings"], "the unmatched line must be announced"

            queued = db.execute(
                text(
                    "SELECT COUNT(*) FROM analytics_recompute_queue "
                    "WHERE job = :j AND bucket_date >= :a AND bucket_date < :b"
                ),
                {"j": JOB, "a": SANDBOX_FROM, "b": SANDBOX_TO},
            ).scalar()
            assert int(queued or 0) == 3

            rows = (
                db.execute(
                    select(PaymentSettlement).where(
                        PaymentSettlement.source_file == name
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 3
            matched = [r for r in rows if r.order_id is not None]
            assert len(matched) == 2
            assert {r.match_key for r in matched} == {SettlementMatchKey.MERCHANT_TXN_ID}

            audit = (
                db.execute(
                    select(AuditEvent).where(
                        AuditEvent.actor_user_id == user.id,
                        AuditEvent.action == "analytics.settlements.upload",
                    )
                )
                .scalars()
                .all()
            )
            assert len(audit) == 1
            extra = audit[0].extra or {}
            assert extra["rows_parsed"] == 3
            assert extra["matched"] == 2
            assert extra["unmatched"] == 1
            assert extra["source_file"] == name
            assert extra["bytes"] == len(body.encode("utf-8"))

            # -- the idempotency headline: an identical re-upload is a no-op --
            before = _snapshot(db)
            again = _upload(client, user, name, body)
            assert again.status_code == 201, again.text
            assert again.json()["rows_new"] == 0
            assert again.json()["rows_parsed"] == 3
            db.expire_all()
            assert _snapshot(db) == before, (
                "re-uploading the identical file changed stored rows — the "
                "upsert key failed and a fee was doubled or rewritten"
            )
        finally:
            db.close()


# ===========================================================================
# 2. Malformed files ingest nothing, through the API      (April 1977)
# ===========================================================================
class TestMalformedUpload:
    def test_malformed_csv_is_422_and_the_table_is_unchanged(self, client, make_user):
        user = make_user(BASE_PERM, FINANCE_PERM, WRITE_PERM)
        db = SessionLocal()
        try:
            before = _snapshot(db)

            # A bad amount on the second row: the whole file must be refused.
            bad_amount = _csv(
                _line("pay_ok_1", credit="100.00", fee="2.00", tax="0.36",
                      created_at="1977-04-05 10:00:00"),
                _line("pay_bad_1", credit="not-a-number", fee="2.00", tax="0.36",
                      created_at="1977-04-05 11:00:00"),
            )
            response = _upload(client, user, "bad-amount.csv", bad_amount)
            assert response.status_code == 422, response.text
            error = response.json()["error"]
            assert error["code"] == "validation_error"
            assert error["details"]["problems"], "every problem must be named"
            assert "nothing was ingested" in error["message"].lower()

            # A file that is not a settlement report at all.
            response = _upload(
                client, user, "wrong-shape.csv", "colour,animal\nred,horse\n"
            )
            assert response.status_code == 422

            # An unknown amount_unit is refused before the parser runs.
            response = _upload(
                client, user, "unit.csv", bad_amount, amount_unit="rupees"
            )
            assert response.status_code == 422
            assert response.json()["error"]["details"]["allowed"] == list(AmountUnit.ALL)

            # Not UTF-8 text.
            response = client.post(
                f"{API_PREFIX}/admin/settlements/upload",
                files={"file": ("binary.csv", b"\xff\xfe\x00broken", "text/csv")},
                data={"amount_unit": AmountUnit.MAJOR},
                headers=user.headers,
            )
            assert response.status_code == 422

            db.expire_all()
            assert _snapshot(db) == before, (
                "a rejected upload changed payment_settlements — atomicity "
                "failed through the API path"
            )
        finally:
            db.close()


# ===========================================================================
# 3. Permission isolation — the lookup itself, then the status codes
# ===========================================================================
class TestPermissionIsolation:
    def test_upload_requires_the_manage_grant(self, client, make_user):
        scoped = make_user(BASE_PERM, OTHER_ANALYTICS_PERM)
        holds = scoped.holds()
        assert holds[OTHER_ANALYTICS_PERM] is True, "the role was never written"
        assert holds[WRITE_PERM] is False
        assert holds[FINANCE_PERM] is False

        response = _upload(client, scoped, "denied.csv", _csv(
            _line("pay_denied", credit="10.00", fee="0.20", tax="0.04",
                  created_at="1977-04-20 10:00:00")
        ))
        assert response.status_code == 403
        with SessionLocal() as db:
            assert (
                db.execute(
                    select(PaymentSettlement.id).where(
                        PaymentSettlement.source_file == "denied.csv"
                    )
                ).first()
                is None
            )

    def test_status_requires_finance_view(self, client, make_user):
        scoped = make_user(BASE_PERM, OTHER_ANALYTICS_PERM)
        response = client.get(
            f"{API_PREFIX}/admin/settlements/status", headers=scoped.headers
        )
        assert response.status_code == 403

        finance = make_user(BASE_PERM, FINANCE_PERM)
        assert finance.holds()[FINANCE_PERM] is True
        response = client.get(
            f"{API_PREFIX}/admin/settlements/status", headers=finance.headers
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["csv_upload_available"] is True
        assert data["api"]["available"] is False, (
            "the status endpoint must keep saying the settlements API is not "
            "built — that is the INTEGRATION_REQUIRED fact, preserved"
        )
        assert data["report_capability_satisfied"] == (data["settlement_rows"] > 0)

    def test_scoped_non_finance_role_gets_403_on_view_64(self, client, make_user):
        scoped = make_user(BASE_PERM, OTHER_ANALYTICS_PERM)
        holds = scoped.holds()
        assert holds[BASE_PERM] is True and holds[FINANCE_PERM] is False
        response = _get_view(client, scoped, date(1977, 4, 1), date(1977, 5, 1))
        assert response.status_code == 403

        finance = make_user(BASE_PERM, FINANCE_PERM)
        response = _get_view(client, finance, date(1977, 4, 1), date(1977, 5, 1))
        assert response.status_code == 200, response.text


# ===========================================================================
# 4. View 64 end to end                                        (May 1977)
# ===========================================================================
class TestView64:
    def test_view_resolves_end_to_end_after_an_upload(self, client, make_user):
        # `analytics.jobs.run` so the GET can send refresh=true: the envelope
        # is cached under (view, filter-hash, tz-gen, tier) with the daily TTL,
        # and without the bypass a second consecutive run of this suite would
        # be served the previous run's envelope — with the previous run's
        # transaction ids in it.
        user = make_user(BASE_PERM, FINANCE_PERM, WRITE_PERM, "analytics.jobs.run")
        db = SessionLocal()
        try:
            product = _create_product(db)
            owner = _owner(db)  # pattern-swept; see TestUpload for why
            _, mtid_a = _create_paid_order(
                db, owner, product, at=datetime(1977, 5, 10, 10, 0)
            )
            _, mtid_b = _create_paid_order(
                db, owner, product, at=datetime(1977, 5, 10, 11, 0)
            )
            # A capture the gateway never settled — the absence the view must
            # surface as first-class table content.
            unsettled_order, _ = _create_paid_order(
                db, owner, product, at=datetime(1977, 5, 10, 12, 0)
            )
            db.commit()

            orphan_txn = f"pay_orphan_{_uid()}"
            name = f"razorpay-may-{_uid()}.csv"
            response = _upload(client, user, name, _csv(
                _line(f"pay_{_uid()}", credit="1000.00", fee="20.00", tax="3.60",
                      created_at="1977-05-10 15:00:00",
                      settled_at="1977-05-12 09:00:00",
                      settlement_id="setl_SAPI2", receipt=mtid_a),
                _line(f"pay_{_uid()}", credit="1000.00", fee="20.00", tax="3.60",
                      created_at="1977-05-10 16:00:00",
                      settled_at="1977-05-12 09:00:00",
                      settlement_id="setl_SAPI2", receipt=mtid_b),
                _line(orphan_txn, credit="500.00", fee="10.00", tax="1.80",
                      created_at="1977-05-11 10:00:00"),
            ))
            assert response.status_code == 201, response.text

            # The worker's job, run here for the exact buckets the upload named.
            for day in response.json()["payment_dates"] + response.json()["settlement_dates"]:
                _run_job(db, date.fromisoformat(day))

            view_response = _get_view(
                client, user, date(1977, 5, 1), date(1977, 6, 1), refresh="true"
            )
            assert view_response.status_code == 200, view_response.text
            envelope = view_response.json()

            assert envelope["availability"] == ViewState.PARTIAL.value
            assert envelope["sources"], "resolved views must report provenance"
            assert envelope["quality"] == MetricQuality.INCOMPLETE.value, (
                "an unmatched line and an unsettled capture exist; the window "
                "is not fully reconciled and must not be ACTUAL"
            )

            days = {r["date"]: r for r in envelope["tables"]["settlement_days"]["rows"]}
            fee_day = days["1977-05-10"]
            cash_day = days["1977-05-12"]
            # Fee side: payment-dated. Two matched captures at 20.00 + 3.60.
            assert Decimal(fee_day["fee_transacted"]) == Decimal("40.00")
            assert Decimal(fee_day["tax_transacted"]) == Decimal("7.20")
            assert fee_day["matched_txns"] == 2
            # The two populations stay apart: no cash on the fee day, no fees
            # on the cash day.
            assert Decimal(fee_day["payout_amount"]) == Decimal("0.00")
            assert Decimal(cash_day["fee_transacted"]) == Decimal("0.00")
            # Cash side: settlement-dated. 2000 - 40 - 7.20 = 1952.80.
            assert Decimal(cash_day["payout_amount"]) == Decimal("1952.80")
            assert cash_day["settlement_batches"] == 1

            # Charts read one family each.
            fee_points = {p["date"]: p for p in envelope["series"]["fees_trend"]}
            assert Decimal(fee_points["1977-05-10"]["fee_transacted"]) == Decimal("40.00")
            payout_points = {p["date"]: p for p in envelope["series"]["payout_trend"]}
            assert Decimal(payout_points["1977-05-12"]["payout_amount"]) == Decimal("1952.80")

            # The reconciliation: four checks, always, matching CheckResult.to_row.
            variances = envelope["tables"]["variances"]["rows"]
            assert len(variances) == 4
            by_check = {row["check_name"]: row for row in variances}
            assert set(by_check) == {
                "settlement_vs_captured",
                "settlement_payout_identity",
                "settlement_unmatched_lines",
                "settlement_unsettled_payments",
            }
            assert by_check["settlement_payout_identity"]["status"] == "matched"
            assert by_check["settlement_unmatched_lines"]["status"] == "variance"
            assert by_check["settlement_unsettled_payments"]["status"] == "variance"
            for row in variances:
                assert set(row) >= {
                    "check_name", "period", "status", "source_value",
                    "rollup_value", "variance_pct", "detail",
                }

            # The two gaps are first-class table content.
            orphans = envelope["tables"]["unmatched_lines"]["rows"]
            assert [r["transaction_id"] for r in orphans] == [orphan_txn]
            outstanding = envelope["tables"]["unsettled_payments"]["rows"]
            assert [r["order_id"] for r in outstanding] == [unsettled_order.id]
        finally:
            db.close()

    def test_view_is_gated_at_runtime_while_no_report_exists(self, client, make_user):
        """The runtime probe: PARTIAL is the ceiling, and with no settlement
        rows the resolver answers `not_configured` — empty sources, the
        NOT_CONFIGURED warning naming the report capability, no data blocks.
        Pinned against a gateway that cannot exist so the assertion holds on a
        shared database whatever other suites have uploaded."""
        user = make_user(BASE_PERM, FINANCE_PERM)
        response = _get_view(
            client, user, date(1977, 5, 1), date(1977, 6, 1),
            payment_gateway=f"gw{_uid()}",
        )
        assert response.status_code == 200, response.text
        envelope = response.json()
        assert envelope["availability"] == ViewState.PARTIAL.value
        assert envelope["sources"] == [], (
            "the machine-readable half of 'nothing is connected' is an empty "
            "sources list"
        )
        assert envelope["quality"] == MetricQuality.INCOMPLETE.value
        assert not envelope["tables"] and not envelope["series"] and not envelope["kpis"]
        gate = [w for w in envelope["warnings"] if w["code"] == "NOT_CONFIGURED"]
        assert gate, "the refusal must say why"
        assert Capability.GATEWAY_SETTLEMENT_REPORT.value in gate[0]["detail"]["requires"]


# ===========================================================================
# 5 + 6. The margin wire                                       (June/July 1977)
# ===========================================================================
class TestMarginPrecedence:
    WINDOW_FROM = date(1977, 6, 10)
    WINDOW_TO = date(1977, 6, 12)

    def test_zero_settlement_rows_change_nothing(self, monkeypatch):
        """The no-op regression every existing margin test implicitly assumes.

        With ZERO settlement rows the ACTUAL-precedence path must contribute
        nothing: the result is compared field-for-field against a run with the
        observed-fee scan stubbed out entirely, so any divergence — a value, a
        quality, a warning, a component ordering — fails here before it fails
        as an unexplained drift in the older suites.
        """
        db = SessionLocal()
        try:
            product = _create_product(db)
            _create_paid_order(db, _owner(db), product, at=datetime(1977, 6, 10, 10, 0))
            _create_paid_order(db, _owner(db), product, at=datetime(1977, 6, 11, 10, 0))
            db.commit()
            _gateway_rule(db)

            assert (
                db.execute(
                    select(PaymentSettlement.id).where(
                        PaymentSettlement.payment_date >= self.WINDOW_FROM,
                        PaymentSettlement.payment_date < self.WINDOW_TO,
                    )
                ).first()
                is None
            ), "sandbox not clean: settlement rows exist in the margin window"

            with_wire = _margin(db).compute(self.WINDOW_FROM, self.WINDOW_TO)

            monkeypatch.setattr(
                MarginService,
                "_observed_gateway_fees",
                lambda self, bases, scope: {},
            )
            without_wire = _margin(db).compute(self.WINDOW_FROM, self.WINDOW_TO)

            assert with_wire == without_wire, (
                "MarginService.compute is not a no-op over zero settlement rows"
            )
            gateway = _component(with_wire, CostType.GATEWAY_FEE)
            # 2% of 2000.00 across the two days = 40.00 = 4000 paise.
            assert gateway.value_minor == 4000
            assert gateway.quality is MetricQuality.ESTIMATED
            assert not any("settlement" in w for w in with_wire.warnings)
        finally:
            db.close()

    def test_full_coverage_replaces_the_estimate_and_partial_blends(self):
        db = SessionLocal()
        try:
            product = _create_product(db)
            owner = _owner(db)
            # Day A — fully covered: both captures settled and matched.
            _, mtid_a1 = _create_paid_order(db, owner, product, at=datetime(1977, 7, 5, 10, 0))
            _, mtid_a2 = _create_paid_order(db, owner, product, at=datetime(1977, 7, 5, 11, 0))
            # Day B — half covered: one capture settled, one missing.
            _, mtid_b1 = _create_paid_order(db, owner, product, at=datetime(1977, 7, 12, 10, 0))
            _create_paid_order(db, owner, product, at=datetime(1977, 7, 12, 11, 0))
            db.commit()
            _gateway_rule(db)  # 2% -> 2000 paise per 1000.00 order

            ingest_settlement_csv(
                db,
                _csv(
                    _line(f"pay_{_uid()}", credit="1000.00", fee="20.00", tax="3.60",
                          created_at="1977-07-05 15:00:00",
                          settled_at="1977-07-07 09:00:00",
                          settlement_id="setl_SAPI3", receipt=mtid_a1),
                    _line(f"pay_{_uid()}", credit="1000.00", fee="20.00", tax="3.60",
                          created_at="1977-07-05 16:00:00",
                          settled_at="1977-07-07 09:00:00",
                          settlement_id="setl_SAPI3", receipt=mtid_a2),
                    _line(f"pay_{_uid()}", credit="1000.00", fee="20.00", tax="3.60",
                          created_at="1977-07-12 15:00:00",
                          settled_at="1977-07-14 09:00:00",
                          settlement_id="setl_SAPI4", receipt=mtid_b1),
                ),
                amount_unit=AmountUnit.MAJOR,
                source_file="sapi-margin.csv",
            )

            # -- full coverage: observed fee+tax replaces the 2% estimate ----
            full = _margin(db).compute(date(1977, 7, 5), date(1977, 7, 6))
            gateway = _component(full, CostType.GATEWAY_FEE)
            assert gateway.value_minor == 4720, (
                "expected the OBSERVED 40.00 fee + 7.20 tax (4720 paise), not "
                f"the 2% estimate (4000): got {gateway.value_minor}"
            )
            assert gateway.quality is MetricQuality.ACTUAL
            assert "payment_settlements" in gateway.source
            assert any("observed settlement fees" in w for w in full.warnings)

            # -- partial coverage: observed on the matched half, the rule on
            #    the remainder, and the blend is NOT ACTUAL -------------------
            partial = _margin(db).compute(date(1977, 7, 12), date(1977, 7, 13))
            gateway = _component(partial, CostType.GATEWAY_FEE)
            # 2360 observed (20.00 + 3.60) + 2% of the uncovered 1000.00 = 2000.
            assert gateway.value_minor == 2360 + 2000
            assert gateway.quality is MetricQuality.ESTIMATED, (
                "a blended day is graded by its weaker half — never ACTUAL"
            )
            assert gateway.quality is not MetricQuality.ACTUAL
            assert "cost rule" in gateway.source

            # -- both days in one window: totals add, worst quality wins -----
            both = _margin(db).compute(date(1977, 7, 5), date(1977, 7, 13))
            gateway = _component(both, CostType.GATEWAY_FEE)
            assert gateway.value_minor == 4720 + 4360
            assert gateway.quality is MetricQuality.ESTIMATED

            # -- a fully covered day no longer NEEDS a rule ------------------
            db.execute(
                text("DELETE FROM analytics_cost_rules WHERE source = :src"),
                {"src": RULE_SOURCE},
            )
            db.commit()
            ruleless = _margin(db).compute(date(1977, 7, 5), date(1977, 7, 6))
            gateway = _component(ruleless, CostType.GATEWAY_FEE)
            assert gateway.value_minor == 4720
            assert gateway.quality is MetricQuality.ACTUAL
            assert CostType.GATEWAY_FEE not in ruleless.missing_inputs
        finally:
            db.close()


def _owner(db: Session) -> int:
    """A minimal order-owning user inside the sweep's email pattern."""
    user = User(
        email=f"sapi-{_uid()}@example.com",
        hashed_password=hash_password("TestPass123!"),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    return user.id
