"""Review trust tests: verified-purchase flag + moderation gate.

Covers:
  1. is_verified_purchase is derived from the author's order history — set
     when the user has a committed order (PAID/SHIPPED/DELIVERED) containing
     the product, not set for PENDING/CANCELLED/REFUNDED orders, no order at
     all, or another user's order.
  2. reviews.auto_approve setting: "false" routes new user reviews into the
     moderation queue (is_approved=False) — hidden from the public listing
     and excluded from the product's rating aggregates until an admin
     approves; absent/"true" preserves today's publish-immediately behavior.
  3. Moderation gate on edits: with auto-approve off, an author editing an
     already-approved review sends it back into the moderation queue (and the
     product aggregates are recomputed without it); with auto-approve on, an
     edit re-publishes immediately as before.

Hermetic: everything runs in-process against in-memory SQLite with a
FakeRedis injected into SettingsService — no live MySQL or Redis needed.
The session-level conftest guard still requires safe env values, so run as:

    ENVIRONMENT=test MYSQL_HOST=localhost pytest tests/test_review_trust.py -v
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401 — registers every table on Base.metadata
from app.models.base import Base
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.review import Review
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.services.review_service import ReviewService
from app.services.settings_service import SettingsService

AUTO_APPROVE_KEY = "reviews.auto_approve"


# ---------------------------------------------------------------------------
# In-memory Redis stub (same pattern as tests/test_profit_service.py)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Always reports a cache miss so SettingsService reads fresh from DB."""

    def get(self, _key):
        return None

    def setex(self, *args, **kwargs):
        pass

    def delete(self, *_args):
        pass


@pytest.fixture(autouse=True)
def _settings_use_fake_redis():
    """Patch SettingsService.__init__ to inject FakeRedis for every test —
    no live Redis, and no 60s cache hiding in-test setting changes."""
    orig = SettingsService.__init__

    def _fake_init(self, db_arg, redis_client=None):
        orig(self, db_arg, redis_client=_FakeRedis())

    with patch.object(SettingsService, "__init__", _fake_init):
        yield


# ---------------------------------------------------------------------------
# Fixtures + factories
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_user(db: Session) -> User:
    user = User(
        email=f"trust-{_uid()}@example.com",
        hashed_password="x" * 60,  # never verified in these tests
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.flush()
    return user


def _make_product(db: Session) -> Product:
    uid = _uid()
    product = Product(
        sku=f"TRUST-{uid}",
        name=f"Trust Test Product {uid}",
        price=Decimal("199.00"),
        stock=10,
    )
    db.add(product)
    db.flush()
    return product


def _make_order(
    db: Session, user: User, product: Product, status: OrderStatus
) -> Order:
    order = Order(
        user_id=user.id,
        status=status,
        subtotal=Decimal("199.00"),
        total_amount=Decimal("199.00"),
    )
    order.items.append(
        OrderItem(product_id=product.id, quantity=1, unit_price=Decimal("199.00"))
    )
    db.add(order)
    db.flush()
    return order


def _set_auto_approve(db: Session, value: str) -> None:
    row = db.execute(
        select(SystemSetting).where(SystemSetting.key == AUTO_APPROVE_KEY)
    ).scalar_one_or_none()
    if row is None:
        row = SystemSetting(key=AUTO_APPROVE_KEY, category="reviews", value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()


def _create_review(db: Session, user: User, product: Product, rating: int = 5) -> Review:
    return ReviewService(db).create_for_user(
        user, product.id, rating, "Nice", "Works as advertised."
    )


# ---------------------------------------------------------------------------
# 1. Verified purchase flag
# ---------------------------------------------------------------------------


def test_verified_flag_set_for_delivered_purchase(db: Session):
    user = _make_user(db)
    product = _make_product(db)
    _make_order(db, user, product, OrderStatus.DELIVERED)

    review = _create_review(db, user, product)

    assert review.is_verified_purchase is True


@pytest.mark.parametrize("status", [OrderStatus.PAID, OrderStatus.SHIPPED])
def test_verified_flag_set_for_committed_states(db: Session, status: OrderStatus):
    """PAID/SHIPPED count too — every order (prepaid AND COD) passes through
    PAID before shipping, so PAID is the earliest committed-purchase state
    (same semantics as CODService._is_first_time_customer)."""
    user = _make_user(db)
    product = _make_product(db)
    _make_order(db, user, product, status)

    review = _create_review(db, user, product)

    assert review.is_verified_purchase is True


@pytest.mark.parametrize(
    "status",
    [OrderStatus.PENDING, OrderStatus.CANCELLED, OrderStatus.REFUNDED],
)
def test_verified_flag_not_set_for_uncommitted_states(
    db: Session, status: OrderStatus
):
    user = _make_user(db)
    product = _make_product(db)
    _make_order(db, user, product, status)

    review = _create_review(db, user, product)

    assert review.is_verified_purchase is False


def test_verified_flag_not_set_without_any_order(db: Session):
    user = _make_user(db)
    product = _make_product(db)

    review = _create_review(db, user, product)

    assert review.is_verified_purchase is False


def test_verified_flag_not_set_for_another_users_order(db: Session):
    buyer = _make_user(db)
    reviewer = _make_user(db)
    product = _make_product(db)
    _make_order(db, buyer, product, OrderStatus.DELIVERED)

    review = _create_review(db, reviewer, product)

    assert review.is_verified_purchase is False


def test_verified_flag_not_set_for_different_product(db: Session):
    user = _make_user(db)
    bought = _make_product(db)
    reviewed = _make_product(db)
    _make_order(db, user, bought, OrderStatus.DELIVERED)

    review = _create_review(db, user, reviewed)

    assert review.is_verified_purchase is False


# ---------------------------------------------------------------------------
# 2. Moderation gate (reviews.auto_approve)
# ---------------------------------------------------------------------------


def test_auto_approve_absent_defaults_to_approved(db: Session):
    """No settings row → behave exactly like today: publish immediately."""
    user = _make_user(db)
    product = _make_product(db)

    review = _create_review(db, user, product)

    assert review.is_approved is True
    items, total = ReviewService(db).list_for_product(
        product.id, offset=0, limit=10, sort="newest", approved_only=True
    )
    assert total == 1
    assert items[0].id == review.id


def test_auto_approve_true_publishes_immediately(db: Session):
    user = _make_user(db)
    product = _make_product(db)
    _set_auto_approve(db, "true")

    review = _create_review(db, user, product)

    assert review.is_approved is True


def test_auto_approve_false_routes_to_moderation_and_hides_from_public(db: Session):
    user = _make_user(db)
    product = _make_product(db)
    _set_auto_approve(db, "false")

    review = _create_review(db, user, product)
    assert review.is_approved is False

    # Hidden from the public listing (items AND total).
    items, total = ReviewService(db).list_for_product(
        product.id, offset=0, limit=10, sort="newest", approved_only=True
    )
    assert total == 0
    assert items == []

    # Excluded from the product's rating aggregates.
    db.refresh(product)
    assert product.rating_count == 0
    assert product.rating_avg == Decimal("0.00")
    assert product.rating_distribution is None

    # Visible in the existing admin moderation queue (approved=False filter).
    pending, pending_total = ReviewService(db).list_admin(
        q=None, product_id=product.id, rating=None, approved=False, offset=0, limit=10
    )
    assert pending_total == 1
    assert pending[0].id == review.id


def test_admin_approval_publishes_and_counts_in_aggregates(db: Session):
    user = _make_user(db)
    product = _make_product(db)
    _make_order(db, user, product, OrderStatus.DELIVERED)
    _set_auto_approve(db, "false")

    review = _create_review(db, user, product, rating=4)
    assert review.is_approved is False
    assert review.is_verified_purchase is True  # moderation doesn't eat the flag

    approved = ReviewService(db).admin_update(review.id, is_approved=True)
    assert approved.is_approved is True

    items, total = ReviewService(db).list_for_product(
        product.id, offset=0, limit=10, sort="newest", approved_only=True
    )
    assert total == 1
    assert items[0].id == review.id

    db.refresh(product)
    assert product.rating_count == 1
    assert product.rating_avg == Decimal("4.00")
    assert product.rating_distribution == {"1": 0, "2": 0, "3": 0, "4": 1, "5": 0}


# ---------------------------------------------------------------------------
# 3. Moderation gate on edits (update_own)
# ---------------------------------------------------------------------------


def test_edit_approved_review_reenters_moderation_when_auto_approve_off(db: Session):
    """Author edits a published review while moderation is on → the edit must
    NOT re-publish instantly: back to the admin queue, hidden from the public
    listing, dropped from the aggregates."""
    user = _make_user(db)
    product = _make_product(db)
    review = _create_review(db, user, product, rating=5)  # auto-approve default
    assert review.is_approved is True
    _set_auto_approve(db, "false")

    updated = ReviewService(db).update_own(
        user, review.id, rating=1, title="Actually terrible", body=None
    )

    assert updated.is_approved is False
    assert updated.rating == 1

    # Hidden from the public listing again.
    items, total = ReviewService(db).list_for_product(
        product.id, offset=0, limit=10, sort="newest", approved_only=True
    )
    assert total == 0
    assert items == []

    # Aggregates recomputed without it.
    db.refresh(product)
    assert product.rating_count == 0
    assert product.rating_avg == Decimal("0.00")
    assert product.rating_distribution is None

    # Waiting where admins look (the existing approved=False queue).
    pending, pending_total = ReviewService(db).list_admin(
        q=None, product_id=product.id, rating=None, approved=False, offset=0, limit=10
    )
    assert pending_total == 1
    assert pending[0].id == review.id


def test_edit_approved_review_stays_published_when_auto_approve_on(db: Session):
    """Auto-approve on (today's behavior): an author edit re-publishes
    immediately and the aggregates track the new rating."""
    user = _make_user(db)
    product = _make_product(db)
    review = _create_review(db, user, product, rating=5)
    _set_auto_approve(db, "true")

    updated = ReviewService(db).update_own(
        user, review.id, rating=3, title=None, body=None
    )

    assert updated.is_approved is True
    db.refresh(product)
    assert product.rating_count == 1
    assert product.rating_avg == Decimal("3.00")


def test_noop_edit_does_not_dequeue_approved_review(db: Session):
    """An update_own call that changes nothing (same values re-submitted)
    must not knock an approved review back into the moderation queue."""
    user = _make_user(db)
    product = _make_product(db)
    review = _create_review(db, user, product, rating=5)
    _set_auto_approve(db, "false")

    updated = ReviewService(db).update_own(
        user, review.id, rating=5, title="Nice", body="Works as advertised."
    )

    assert updated.is_approved is True


def test_auto_approve_false_does_not_gate_admin_created_reviews(db: Session):
    """admin_create passes is_approved explicitly — the moderation switch only
    applies to user-submitted reviews."""
    product = _make_product(db)
    _set_auto_approve(db, "false")

    review = ReviewService(db).admin_create(
        product_id=product.id,
        rating=5,
        author_name="Seeded Author",
        title=None,
        body=None,
        is_verified_purchase=False,
        is_approved=True,
    )

    assert review.is_approved is True
