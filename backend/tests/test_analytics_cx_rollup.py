"""Tests for ``agg_cx_daily`` — the reviews + inbound-contact rollup.

Covers the ``cx_daily`` aggregation job, the schema it writes into, and the two
views bound to it (51 Reviews and Ratings, 50 Customer Support and Complaint).

What actually has to be true here
---------------------------------
A rollup that computes the wrong number is caught the first time somebody opens
the dashboard. The failures that are never caught are the ones these tests are
built around:

  * ``test_cx_daily_is_idempotent`` — the same bucket, twice, identical column
    values. Not "no error" and not "one row": the numbers. An
    ``ON DUPLICATE KEY UPDATE col = col + VALUES(col)`` passes everything else in
    this file and fails this.
  * ``test_cx_daily_removes_a_product_whose_only_review_disappeared`` — this
    table is dimensioned by product, so a key can vanish: delete the only review
    a product ever had and its row must go with it. Delete-then-insert is the
    only write pattern that can express that; an upsert leaves the vacated row
    on the "products pulling the average down" table forever, at its old numbers.
  * ``test_cx_daily_bucketing_is_store_local`` — two reviews sharing a **UTC**
    date and belonging to two different **store-local** reporting days. 23:00 IST
    is 17:30 UTC the same day; 00:30 IST is 19:00 UTC the day before. A UTC
    bucketer puts both together and is wrong by 5.5 hours of feedback every day.
  * ``test_a_weeks_average_from_stored_counts_equals_the_true_average`` — the
    reason this table stores five star counts and a ``rating_sum`` instead of an
    ``avg_rating``. The same fixture is averaged both ways and only one of them
    is right; the wrong one is off by an amount no reader could detect.

Then the two schema claims, which fail in production and nowhere else if they
are not asserted here: the ORM matches the database, and the hand-applied twin
SQL matches the Alembic revision. Production applies reviewed SQL and never runs
alembic (DEPLOY.md §6), so those two artifacts are the only thing standing
between CI-green and an analytics endpoint that 500s on the live store.

Isolation strategy
------------------
Every fixture lives in **1998**, a year this store has never traded in and which
no other analytics suite uses (2001, 2003-2015 and 2026 are taken). Review
assertions are additionally scoped by **product**: each test mints its own
products, so the rows it asserts on are keyed by something it invented and a
concurrent copy of this suite can neither move the numbers nor be counted by
them.

The store-wide message row (``product_id = 0``) cannot be scoped that way — its
key is shared by construction — so the message tests carry a loud precondition
instead: ``_assert_no_foreign_messages`` fails if anything else already sits in
the bucket. A clear failure beats a number that is quietly wrong.

Every review is written with ``user_id = NULL`` (which ``reviews`` permits
without limit, so admin-entered reviews are unrestricted) so no user rows are
created and no shared account is touched.

No db fixture exists in ``conftest.py``; each test owns its ``SessionLocal()``
and tears down in a ``finally`` through a fresh session.

Run inside the analytics container:

    docker exec wvana-py python -m pytest tests/test_analytics_cx_rollup.py -q
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import SessionLocal
from app.models.analytics_base import DIMENSION_UNKNOWN
from app.models.analytics_cx import (
    MESSAGE_STATUS_COLUMNS,
    RATING_COLUMNS,
    STORE_WIDE_PRODUCT_ID,
    AggCxDaily,
)
from app.models.contact_message import ContactMessage
from app.models.product import Category, Product
from app.models.review import Review
from app.repositories.analytics_repository import columns_for, known_sources, measures_for
from app.schemas.analytics_view import AnalyticsViewEnvelope
from app.services.analytics import registry
from app.services.analytics.aggregation import AggregationRunner
from app.services.analytics.filters import AnalyticsFilters, Comparison, Period
from app.services.analytics.metric_kind import MetricKind, classify
from app.services.analytics.resolvers.base import NOT_CONFIGURED
from app.services.analytics.timebox import active_generation, day_bounds_utc, store_timezone
from app.services.analytics.view_service import AnalyticsViewService

# ---------------------------------------------------------------------------
# The 1998 sandbox
# ---------------------------------------------------------------------------

SANDBOX_FIRST = date(1998, 1, 1)
SANDBOX_LAST = date(1998, 12, 31)

DAY_MAIN = date(1998, 3, 4)
DAY_GONE = date(1998, 3, 9)
#: 23:00 IST here and 00:30 IST on the next day share a UTC date.
DAY_TZ_LATE = date(1998, 3, 12)
DAY_TZ_EARLY = date(1998, 3, 13)

WEEK_START = date(1998, 4, 6)
WEEK_DAYS = 7

#: The window the two views are resolved over. Kept well clear of the job days so
#: a rollup row seeded here can never be mistaken for one the job wrote.
VIEW_START = date(1998, 6, 1)
VIEW_DAYS = 3

TABLE_NAME = AggCxDaily.__tablename__
TWIN_SQL = (
    Path(__file__).resolve().parents[1] / "scripts" / "sql" / "2026-07-29_agg_cx_daily.sql"
)

#: The analytics module the two CX views live in.
CX_MODULE = "customer-experience"

#: Distinctive enough that teardown can delete this module's run log without a
#: date filter, and that a stuck row in a shared DB is traceable to these tests.
WORKER_ID = "test-agg-cx"

#: Bookkeeping, not measurement. Two runs of the same bucket differ in these and
#: must be identical in everything else.
_NON_MEASURE_COLUMNS = frozenset({"id", "computed_at"})

_DELETED_AT_LEAST = (
    "Pattern B deletes before it reinserts, so a rerun must delete at least the "
    "rows this test owns; an upsert deletes none of them and leaves a vacated "
    "key behind at its old numbers"
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _at(db: Session, day: date, hour: int, minute: int = 0) -> datetime:
    """The UTC instant that is ``hour:minute`` **store-local** on ``day``.

    Built from ``timebox.day_bounds_utc`` rather than by hand, so the bucketing
    assertions are about bucketing and not about two independent copies of the
    same arithmetic.
    """
    start, _ = day_bounds_utc(day, store_timezone(db))
    return start + timedelta(hours=hour, minutes=minute)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


class _Owned:
    """Ids this test created, so teardown removes exactly them."""

    def __init__(self) -> None:
        self.products: list[int] = []
        self.categories: list[int] = []
        self.reviews: list[int] = []
        self.messages: list[int] = []


def _create_category(db: Session, owned: _Owned) -> Category:
    suffix = _uid()
    category = Category(name=f"CX Category {suffix}", slug=f"cx-category-{suffix}")
    db.add(category)
    db.flush()
    owned.categories.append(category.id)
    return category


def _create_product(
    db: Session, owned: _Owned, *, category: Category | None = None
) -> Product:
    product = Product(
        sku=f"SKU-CX-{_uid()}",
        name=f"CxProduct {_uid()}",
        price=Decimal("499.00"),
        cost=Decimal("200.00"),
        stock=25,
        category_id=None if category is None else category.id,
    )
    db.add(product)
    db.flush()
    owned.products.append(product.id)
    return product


def _create_review(
    db: Session,
    owned: _Owned,
    product: Product,
    *,
    created_at: datetime,
    rating: int,
    is_approved: bool = True,
    is_verified_purchase: bool = False,
    helpful_count: int = 0,
) -> Review:
    """One review, always with ``user_id = NULL``.

    ``reviews`` has UNIQUE (product_id, user_id) and MySQL does not treat NULLs
    as duplicates, so admin-entered reviews are unrestricted — which is what lets
    a fixture put twenty reviews on one product without inventing twenty users.
    """
    review = Review(
        product_id=product.id,
        user_id=None,
        author_name=f"CX Reviewer {_uid()}",
        rating=rating,
        title="fixture",
        body="fixture review",
        is_verified_purchase=is_verified_purchase,
        is_approved=is_approved,
        helpful_count=helpful_count,
        created_at=created_at,
    )
    db.add(review)
    db.flush()
    owned.reviews.append(review.id)
    return review


def _create_message(
    db: Session, owned: _Owned, *, created_at: datetime, status: str
) -> ContactMessage:
    message = ContactMessage(
        name=f"CX Sender {_uid()}",
        email=f"cx-{_uid()}@example.com",
        subject="fixture enquiry",
        message="fixture body",
        status=status,
        created_at=created_at,
    )
    db.add(message)
    db.flush()
    owned.messages.append(message.id)
    return message


# ---------------------------------------------------------------------------
# Teardown + preconditions
# ---------------------------------------------------------------------------


def _cleanup(owned: _Owned) -> None:
    """Delete everything this test owned, through a fresh session.

    A fresh session so teardown cannot be skipped by a half-rolled-back
    transaction in the test's own session. The rollup and run-log rows go by
    sandbox date / worker id, because they are written by Core statements the
    test never sees the ids of.
    """
    with SessionLocal() as session:
        for table, ids in (
            ("reviews", owned.reviews),
            ("contact_messages", owned.messages),
        ):
            if ids:
                session.execute(
                    text(f"DELETE FROM {table} WHERE id IN :ids"), {"ids": tuple(ids)}
                )
        if owned.products:
            session.execute(
                text("DELETE FROM reviews WHERE product_id IN :ids"),
                {"ids": tuple(owned.products)},
            )
            session.execute(
                text("DELETE FROM products WHERE id IN :ids"),
                {"ids": tuple(owned.products)},
            )
        if owned.categories:
            session.execute(
                text("DELETE FROM categories WHERE id IN :ids"),
                {"ids": tuple(owned.categories)},
            )

        window = {"first": SANDBOX_FIRST, "last": SANDBOX_LAST}
        for table in (TABLE_NAME, "analytics_recompute_queue"):
            session.execute(
                text(f"DELETE FROM {table} WHERE bucket_date BETWEEN :first AND :last"),
                window,
            )
        session.execute(
            text("DELETE FROM analytics_sync_runs WHERE worker_id = :worker"),
            {"worker": WORKER_ID},
        )
        session.commit()


def _assert_no_foreign_messages(db: Session, day: date) -> None:
    """Fail loudly if a message this test did not create sits in the bucket.

    The store-wide row is the one key here that cannot be scoped to something the
    test invented: every message in a day lands on ``product_id = 0``. A stray
    row would not make these assertions noisy — it would make them wrong.
    """
    start, end = day_bounds_utc(day, store_timezone(db))
    foreign = db.execute(
        select(ContactMessage.id)
        .where(ContactMessage.created_at >= start, ContactMessage.created_at < end)
        .limit(5)
    ).scalars().all()
    assert not foreign, (
        f"contact_messages {foreign} already sit in the {day} bucket; the "
        "store-wide row is a shared key and these tests assert absolute figures"
    )


# ---------------------------------------------------------------------------
# Reading rollup rows back
# ---------------------------------------------------------------------------


def _measures(row: AggCxDaily) -> dict:
    """Every measured column of a rollup row, keyed by column name."""
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name not in _NON_MEASURE_COLUMNS
    }


def _rows(db: Session, bucket: date, generation: int) -> dict[int, AggCxDaily]:
    db.expire_all()
    return {
        int(row.product_id): row
        for row in db.execute(
            select(AggCxDaily).where(
                AggCxDaily.bucket_date == bucket,
                AggCxDaily.tz_generation == generation,
            )
        ).scalars().all()
    }


def _runner(db: Session) -> AggregationRunner:
    return AggregationRunner(db, worker_id=WORKER_ID)


def _derived_average(row) -> Decimal:
    """The average this table is designed to be read through."""
    return Decimal(row.rating_sum) / Decimal(row.rated_reviews)


# ===========================================================================
# The job
# ===========================================================================


def _main_fixture(db: Session, owned: _Owned, day: date) -> tuple[Product, Product]:
    """One well-reviewed product, one barely-reviewed one, and three messages.

    Product A: 5, 5, 4, 1 — one of them unapproved, two verified purchases and
    11 helpful votes between them. Product B: a single 3.
    """
    category = _create_category(db, owned)
    product_a = _create_product(db, owned, category=category)
    product_b = _create_product(db, owned)

    for rating, approved, verified, helpful in (
        (5, True, True, 7),
        (5, True, False, 4),
        (4, True, True, 0),
        (1, False, False, 0),
    ):
        _create_review(
            db,
            owned,
            product_a,
            created_at=_at(db, day, 10),
            rating=rating,
            is_approved=approved,
            is_verified_purchase=verified,
            helpful_count=helpful,
        )
    _create_review(db, owned, product_b, created_at=_at(db, day, 11), rating=3)

    for status in ("new", "replied", "escalated"):
        _create_message(db, owned, created_at=_at(db, day, 12), status=status)

    db.commit()
    return product_a, product_b


def test_cx_daily_is_idempotent_with_realistic_values() -> None:
    """Two runs leave identical counters. This is the test an accumulating
    upsert fails and every other test in this file passes."""
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        _assert_no_foreign_messages(db, DAY_MAIN)
        product_a, product_b = _main_fixture(db, owned, DAY_MAIN)
        mine = {product_a.id, product_b.id, STORE_WIDE_PRODUCT_ID}

        def owned_rows() -> dict:
            return {
                product_id: _measures(row)
                for product_id, row in _rows(db, DAY_MAIN, generation).items()
                if product_id in mine
            }

        runner = _runner(db)
        first = runner.run_bucket("cx_daily", DAY_MAIN)
        before = owned_rows()

        second = runner.run_bucket("cx_daily", DAY_MAIN)
        after = owned_rows()

        assert set(before) == mine, (
            "both reviewed products and the store-wide message row must exist; "
            f"got {sorted(before)}"
        )
        assert second.rows_deleted >= len(mine), _DELETED_AT_LEAST
        assert before == after, (
            "re-running one bucket changed its values; the write is accumulating "
            f"rather than replacing. before={before} after={after}"
        )
        assert first.rows_written == second.rows_written >= 3

        a = before[product_a.id]
        assert a["reviews_submitted"] == 4
        assert a["reviews_approved"] == 3
        assert a["reviews_unapproved"] == 1, (
            "one boolean cannot tell a rejection from an unmoderated review, "
            "which is why the column is not called reviews_rejected"
        )
        assert a["reviews_approved"] + a["reviews_unapproved"] == a["reviews_submitted"]
        assert a["rating_5"] == 2 and a["rating_4"] == 1 and a["rating_1"] == 1
        assert a["rating_2"] == 0 and a["rating_3"] == 0
        assert a["rating_sum"] == 15 and a["rated_reviews"] == 4
        assert a["verified_purchase_reviews"] == 2, "the numerator; never the share"
        assert a["helpful_votes"] == 11
        assert a["sku_snapshot"] != DIMENSION_UNKNOWN
        assert a["category_id_snapshot"] is not None
        assert all(a[column] == 0 for column in _MESSAGE_COLUMNS), (
            "a review is not a message: the message columns must be zero on a "
            "product row, or summing them would double count"
        )

        b = before[product_b.id]
        assert b["reviews_submitted"] == 1 and b["rating_3"] == 1
        assert b["category_id_snapshot"] is None, "this product has no category"

        store_wide = before[STORE_WIDE_PRODUCT_ID]
        assert store_wide["messages_received"] == 3
        assert store_wide["messages_new"] == 1
        assert store_wide["messages_replied"] == 1
        assert store_wide["messages_closed"] == 0
        assert store_wide["messages_other"] == 1, (
            "'escalated' is outside the known vocabulary; contact_messages.status "
            "is free-form, so it is counted as unclassified rather than folded "
            "into messages_new and read as unanswered"
        )
        assert (
            store_wide["messages_new"]
            + store_wide["messages_replied"]
            + store_wide["messages_closed"]
            + store_wide["messages_other"]
            == store_wide["messages_received"]
        ), "the status columns must partition the total exactly"
        assert store_wide["reviews_submitted"] == 0
        assert store_wide["sku_snapshot"] == DIMENSION_UNKNOWN

        assert any("cx_unknown_message_status" in w for w in second.warnings), (
            "an unrecognised status must be named in the run log, not silently "
            "bucketed"
        )
    finally:
        _cleanup(owned)
        db.close()


_MESSAGE_COLUMNS = ("messages_received", *MESSAGE_STATUS_COLUMNS.values(), "messages_other")


def test_cx_daily_removes_a_product_whose_only_review_disappeared() -> None:
    """Pattern B: a key that vanishes must leave the bucket.

    An upsert cannot express this. The row would survive at its old numbers and
    keep a deleted one-star review on the "products pulling the average down"
    table indefinitely.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        doomed = _create_product(db, owned)
        survivor = _create_product(db, owned)
        review = _create_review(
            db, owned, doomed, created_at=_at(db, DAY_GONE, 9), rating=1
        )
        _create_review(db, owned, survivor, created_at=_at(db, DAY_GONE, 9), rating=5)
        db.commit()

        runner = _runner(db)
        runner.run_bucket("cx_daily", DAY_GONE)
        before = _rows(db, DAY_GONE, generation)
        assert doomed.id in before and survivor.id in before
        assert before[doomed.id].rating_1 == 1

        db.execute(text("DELETE FROM reviews WHERE id = :id"), {"id": review.id})
        db.commit()

        result = runner.run_bucket("cx_daily", DAY_GONE)
        after = _rows(db, DAY_GONE, generation)

        assert doomed.id not in after, (
            "the product's only review is gone, so its row must be gone; an "
            "upsert would leave it behind showing a one-star review that no "
            "longer exists"
        )
        assert survivor.id in after, "the rest of the bucket must be rebuilt intact"
        assert after[survivor.id].rating_5 == 1
        assert result.rows_deleted >= 2, _DELETED_AT_LEAST
    finally:
        _cleanup(owned)
        db.close()


def test_cx_daily_bucketing_is_store_local() -> None:
    """Two reviews on the same UTC date, in two different store-local days.

    23:00 IST on day D is 17:30 UTC on D; 00:30 IST on D+1 is 19:00 UTC on D. A
    UTC bucketer puts both in D and is wrong by 5.5 hours of feedback every day,
    in a way no later check can detect.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        product = _create_product(db, owned)
        late = _at(db, DAY_TZ_LATE, 23)
        early = _at(db, DAY_TZ_EARLY, 0, 30)
        assert late.date() == early.date(), (
            "the fixture is only meaningful if the two instants share a UTC date"
        )
        _create_review(db, owned, product, created_at=late, rating=2)
        _create_review(db, owned, product, created_at=early, rating=5)
        db.commit()

        runner = _runner(db)
        runner.run_bucket("cx_daily", DAY_TZ_LATE)
        runner.run_bucket("cx_daily", DAY_TZ_EARLY)

        first = _rows(db, DAY_TZ_LATE, generation)[product.id]
        second = _rows(db, DAY_TZ_EARLY, generation)[product.id]

        assert first.reviews_submitted == 1 and first.rating_2 == 1, (
            "the 23:00 IST review belongs to that IST day"
        )
        assert second.reviews_submitted == 1 and second.rating_5 == 1, (
            "the 00:30 IST review belongs to the NEXT IST day, even though it "
            "shares a UTC date with the first"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_rating_distribution_sums_to_the_review_count_and_derives_the_average() -> None:
    """The five counts are complete, and they give the average back exactly.

    ``rating_1 + ... + rating_5 == rated_reviews`` is what makes the distribution
    a distribution rather than a sample of one, and ``rating_sum`` is checked
    against the weighted sum of those counts so the redundancy stays a
    cross-check instead of becoming a second source of truth.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        _assert_no_foreign_messages(db, DAY_MAIN)
        product_a, _product_b = _main_fixture(db, owned, DAY_MAIN)

        _runner(db).run_bucket("cx_daily", DAY_MAIN)
        row = _rows(db, DAY_MAIN, generation)[product_a.id]

        counts = {value: getattr(row, column) for value, column in RATING_COLUMNS.items()}
        assert sum(counts.values()) == row.rated_reviews == row.reviews_submitted, (
            "every review carried a rating in 1..5, so all three must agree; a "
            "distribution that does not sum to its population is a distribution "
            "of something else"
        )
        assert row.rating_sum == sum(value * n for value, n in counts.items()), (
            "rating_sum must equal the weighted sum of the five counts — it is a "
            "cross-check, not an independent number"
        )
        # 5 + 5 + 4 + 1 = 15 over 4 reviews. Hand-calculated, not recomputed from
        # the same expression the assertion above already checked.
        assert row.rating_sum == 15 and row.rated_reviews == 4
        assert _derived_average(row) == Decimal("3.75")
    finally:
        _cleanup(owned)
        db.close()


def test_a_weeks_average_from_stored_counts_equals_the_true_average() -> None:
    """Why five counts beat a stored mean, demonstrated rather than asserted.

    Seven days of wildly uneven volume: one day with a single 1-star review, one
    with twenty 5-star reviews, and five quiet days. The week's true average is
    ``SUM(stars) / COUNT(reviews)``. Read off the stored columns as
    ``SUM(rating_sum) / SUM(rated_reviews)`` it comes back exactly. Averaged the
    way a stored ``avg_rating`` column forces — the mean of the daily means — it
    is off by more than a whole star, and nothing about the number looks wrong.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = active_generation(db).generation
        product = _create_product(db, owned)

        # (day offset, rating, how many)
        plan = (
            (0, 1, 1),
            (1, 5, 20),
            (2, 4, 2),
            (3, 3, 1),
            (4, 5, 3),
            (5, 2, 1),
            (6, 4, 4),
        )
        for offset, rating, count in plan:
            day = WEEK_START + timedelta(days=offset)
            for _ in range(count):
                _create_review(
                    db, owned, product, created_at=_at(db, day, 14), rating=rating
                )
        db.commit()

        runner = _runner(db)
        for offset in range(WEEK_DAYS):
            runner.run_bucket("cx_daily", WEEK_START + timedelta(days=offset))

        db.expire_all()
        rows = db.execute(
            select(AggCxDaily).where(
                AggCxDaily.product_id == product.id,
                AggCxDaily.tz_generation == generation,
                AggCxDaily.bucket_date >= WEEK_START,
                AggCxDaily.bucket_date < WEEK_START + timedelta(days=WEEK_DAYS),
            )
        ).scalars().all()
        assert len(rows) == WEEK_DAYS, "every day of the week produced a row"

        true_total = sum(rating * count for _o, rating, count in plan)
        true_count = sum(count for _o, _r, count in plan)
        true_average = Decimal(true_total) / Decimal(true_count)

        from_sums = Decimal(sum(r.rating_sum for r in rows)) / Decimal(
            sum(r.rated_reviews for r in rows)
        )
        assert from_sums == true_average, (
            "the stored sum+count pair must re-bucket to the true weekly average"
        )

        # The five counts alone are enough: rating_sum is a convenience, not an
        # extra input. If the two ever disagreed, one of them would be wrong and
        # nothing downstream could tell which.
        from_counts_total = sum(
            value * sum(getattr(r, column) for r in rows)
            for value, column in RATING_COLUMNS.items()
        )
        from_counts = Decimal(from_counts_total) / Decimal(
            sum(r.rated_reviews for r in rows)
        )
        assert from_counts == true_average

        # And the number a stored daily average would have produced.
        daily_means = [_derived_average(r) for r in rows]
        mean_of_means = sum(daily_means) / Decimal(len(daily_means))
        assert mean_of_means != true_average, (
            "this fixture exists to make the two disagree; if they ever match, "
            "the test has stopped proving anything"
        )
        assert abs(mean_of_means - true_average) > Decimal("1"), (
            f"averaging daily averages gives {mean_of_means} against a true "
            f"{true_average} — more than a whole star out, on data that looks "
            "entirely reasonable. That is why this table stores counts."
        )
    finally:
        _cleanup(owned)
        db.close()


# ===========================================================================
# The views
# ===========================================================================


class _AnalyticsReader:
    """Holds exactly the permission the CX views need, and nothing else.

    ``AnalyticsViewService`` only ever calls ``has_permission``, so this
    exercises the real authorisation path without creating a user row — and
    without reaching for a shared admin account, whose ``is_admin`` short circuit
    would make every check pass for the wrong reason.
    """

    is_admin = False

    def __init__(self, permissions: set[str]) -> None:
        self._permissions = permissions

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions


def _seed_view_rows(db: Session, generation: int, product_ids: tuple[int, int]) -> None:
    """Three days of rollup rows, written directly.

    Directly rather than through the job so that a failure here is a failure of
    the view wiring, not of the aggregation — the job has its own tests above.
    """
    hot, cold = product_ids
    for offset in range(VIEW_DAYS):
        day = VIEW_START + timedelta(days=offset)
        db.add(
            AggCxDaily(
                bucket_date=day,
                product_id=hot,
                tz_generation=generation,
                sku_snapshot=f"SKU-VIEW-{hot}",
                reviews_submitted=10,
                reviews_approved=10,
                rating_5=8,
                rating_4=2,
                rating_sum=8 * 5 + 2 * 4,
                rated_reviews=10,
                verified_purchase_reviews=6,
                helpful_votes=12,
            )
        )
        db.add(
            AggCxDaily(
                bucket_date=day,
                product_id=cold,
                tz_generation=generation,
                sku_snapshot=f"SKU-VIEW-{cold}",
                reviews_submitted=2,
                reviews_approved=1,
                reviews_unapproved=1,
                rating_1=2,
                rating_sum=2,
                rated_reviews=2,
            )
        )
        db.add(
            AggCxDaily(
                bucket_date=day,
                product_id=STORE_WIDE_PRODUCT_ID,
                tz_generation=generation,
                messages_received=5,
                messages_new=2,
                messages_replied=2,
                messages_closed=1,
            )
        )
    db.commit()


def _view_filters() -> AnalyticsFilters:
    return AnalyticsFilters(
        period=Period.CUSTOM,
        date_from=VIEW_START,
        date_to=VIEW_START + timedelta(days=VIEW_DAYS),
        comparison=Comparison.NONE,
    )


def test_view_51_reviews_and_ratings_resolves_with_real_values() -> None:
    """View 51 is declared LIVE and must actually answer.

    Before this rollup existed it broke down by ``rating``, which no rollup
    stores as a dimension, so it returned NOT_CONFIGURED on every request — a
    LIVE badge over an empty screen. The table is the substance of the view: the
    products, how many reviews each got, the average derived from the stored
    sums, and the one-star count that says which product is the problem.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = int(active_generation(db).generation)
        hot = _create_product(db, owned)
        cold = _create_product(db, owned)
        db.commit()
        _seed_view_rows(db, generation, (hot.id, cold.id))

        view = registry.get_view(CX_MODULE, "reviews-and-ratings")
        assert view is not None and view.number == 51
        service = AnalyticsViewService(db, _AnalyticsReader({view.permission}))
        envelope = service.resolve_view(
            CX_MODULE, "reviews-and-ratings", _view_filters(), use_cache=False
        )

        assert isinstance(envelope, AnalyticsViewEnvelope)
        assert envelope.sources, (
            "an empty source list is how this subsystem says 'nothing is wired "
            "up'; the view is bound to agg_cx_daily and must report provenance"
        )
        assert {s.id for s in envelope.sources} == {TABLE_NAME}
        codes = {w.code for w in envelope.warnings}
        assert NOT_CONFIGURED not in codes, "; ".join(
            w.message for w in envelope.warnings if w.code == NOT_CONFIGURED
        )

        rows = {row["product"]: row for row in envelope.tables["rated_products"].rows}
        assert {hot.id, cold.id} <= set(rows)

        # 3 days x 10 reviews, 3 x (8x5 + 2x4) stars over 3 x 10 rated.
        assert rows[hot.id]["reviews"] == 30
        assert rows[hot.id]["avg_rating"] == Decimal("4.8000")
        assert rows[hot.id]["one_star"] == 0
        assert rows[cold.id]["reviews"] == 6
        assert rows[cold.id]["avg_rating"] == Decimal("1.0000")
        assert rows[cold.id]["one_star"] == 6, (
            "the products pulling the average down are the point of this table"
        )

        store_wide = rows.get(STORE_WIDE_PRODUCT_ID)
        assert store_wide is not None, (
            "the store-wide message row shares this rollup and no filter exists "
            "at this layer, so it appears here — asserted rather than hidden"
        )
        assert store_wide["reviews"] == 0
        assert store_wide["avg_rating"] is None, (
            "no reviews means no average; an undefined ratio must be null and "
            "never 0, which would read as the worst-rated product in the store"
        )
    finally:
        _cleanup(owned)
        db.close()


def test_view_50_support_resolves_volume_and_stays_honest_about_ticketing() -> None:
    """View 50 answers with the one thing a contact form can prove: volume.

    And it must keep saying what it cannot show. ``contact_messages`` has no
    assignee, no reply timestamp and no closure timestamp, so the view stays
    PARTIAL, keeps SUPPORT_TICKETING in ``requires`` and keeps its limitation.
    The message-grain table and the topic chart stay unfilled rather than being
    fed day-grain rows under headings that promise individual messages.
    """
    owned = _Owned()
    db = SessionLocal()
    try:
        generation = int(active_generation(db).generation)
        hot = _create_product(db, owned)
        cold = _create_product(db, owned)
        db.commit()
        _seed_view_rows(db, generation, (hot.id, cold.id))

        view = registry.get_view(CX_MODULE, "customer-support-and-complaint")
        assert view is not None and view.number == 50
        service = AnalyticsViewService(db, _AnalyticsReader({view.permission}))
        envelope = service.resolve_view(
            CX_MODULE, "customer-support-and-complaint", _view_filters(), use_cache=False
        )

        assert isinstance(envelope, AnalyticsViewEnvelope)
        assert envelope.sources and {s.id for s in envelope.sources} == {TABLE_NAME}
        codes = {w.code for w in envelope.warnings}
        assert NOT_CONFIGURED not in codes, "; ".join(
            w.message for w in envelope.warnings if w.code == NOT_CONFIGURED
        )

        points = envelope.series["messages_trend"]
        assert len(points) == VIEW_DAYS
        assert [p["date"] for p in points] == [
            (VIEW_START + timedelta(days=offset)).isoformat()
            for offset in range(VIEW_DAYS)
        ]
        assert all(p["messages"] == Decimal("5") for p in points), (
            "five messages a day, summed across the day's rows — the counters "
            "are zero on every product row, so no double counting"
        )

        assert "message_topics" not in envelope.series, (
            "contact_messages has a free-text subject and no topic taxonomy; an "
            "absent chart is honest, a keyword guess rendered as a distribution "
            "is not"
        )
        assert not envelope.tables, (
            "'Recent messages' is message grain and no rollup stores it. Filling "
            "it with day rows would publish daily aggregates under a heading "
            "that promises individual messages"
        )
        assert view.state.value == "PARTIAL"
        assert "support_ticketing" in [c.value for c in view.requires]
        assert view.limitation
    finally:
        _cleanup(owned)
        db.close()


def test_the_cx_bindings_name_only_stored_summable_columns() -> None:
    """Both views' params resolve against the repository's reflected allowlist.

    The peer suite asserts this across every bound view; it is restated here so
    a change to this rollup's columns fails in this file, next to the model it
    belongs to, rather than in a parametrised sweep elsewhere.
    """
    assert TABLE_NAME in known_sources()
    available = set(columns_for(TABLE_NAME))
    summable = set(measures_for(TABLE_NAME))

    for module_slug, view_slug in (
        (CX_MODULE, "reviews-and-ratings"),
        (CX_MODULE, "customer-support-and-complaint"),
    ):
        view = registry.get_view(module_slug, view_slug)
        assert view is not None
        assert view.params.get("source") == TABLE_NAME
        for metric_id, spec in view.params["metrics"].items():
            for key in ("add", "sub", "over", "over_add", "over_sub"):
                for column in spec.get(key) or ():
                    assert column in available, f"{view_slug}/{metric_id}: {column}"
                    assert column in summable, (
                        f"{view_slug}/{metric_id} sums {column!r}, which the "
                        "repository will not put inside SUM()"
                    )

    assert "product_id" in available, (
        "view 51 groups by the `product` dimension, which resolves to product_id"
    )


def test_every_measure_on_this_rollup_is_a_flow() -> None:
    """Nothing here is a level, a ratio or a distinct count.

    Every params column is summed over the window for a card and over the bucket
    for a series, so a level added across days would be wrong by the length of
    the window and would not raise. `metric_kind.classify` is name-based, which
    is also a check that these columns are named the way the schema names things.
    """
    for column in sorted(measures_for(TABLE_NAME)):
        assert classify(column, source=TABLE_NAME) is MetricKind.FLOW, (
            f"{TABLE_NAME}.{column} does not classify as a flow"
        )


# ===========================================================================
# Schema: ORM vs database, and the migration vs its hand-applied twin
# ===========================================================================


def test_orm_matches_the_database_and_obeys_the_rollup_conventions() -> None:
    """The live table is what ``AggCxDaily`` says it is, and follows the rules.

    The four conventions ``tests/test_analytics_schema.py`` enforces over
    ``analytics_rollups`` apply here too; this rollup lives in its own module, so
    they are asserted here rather than inherited.
    """
    db = SessionLocal()
    try:
        inspector = inspect(db.get_bind())
        assert inspector.has_table(TABLE_NAME), (
            f"{TABLE_NAME} is declared in the ORM but missing from the database. "
            "Run `alembic upgrade f2a91c4e7b30`, or apply "
            "backend/scripts/sql/2026-07-29_agg_cx_daily.sql on the shared DB."
        )

        table = AggCxDaily.__table__
        actual = {c["name"]: c for c in inspector.get_columns(TABLE_NAME)}
        missing = sorted(set(table.columns.keys()) - set(actual))
        assert not missing, f"columns in ORM but not in DB: {missing}"
        extra = sorted(set(actual) - set(table.columns.keys()))
        assert not extra, f"columns in DB but not in the ORM: {extra}"

        for column in table.columns:
            assert _family(str(actual[column.name]["type"])) == _family(str(column.type)), (
                f"{column.name}: ORM says {column.type}, database has "
                f"{actual[column.name]['type']}"
            )

        # Convention 1: no foreign keys — rollups stay independently truncatable.
        assert not table.foreign_keys and not inspector.get_foreign_keys(TABLE_NAME)
        # No floating point: nothing here is money, but the rule is the schema's.
        assert not [
            c.name
            for c in table.columns
            if any(k in str(c.type).upper() for k in ("FLOAT", "DOUBLE", "REAL"))
        ]
        # Convention 2 + 3: one UNIQUE key, it carries tz_generation, and nothing
        # in it is nullable (MySQL allows many NULLs under UNIQUE, which would
        # defeat the idempotency key and let the job double-count).
        unique = _unique_constraint(table)
        assert "tz_generation" in unique
        assert not [c.name for c in table.columns if c.name in unique and c.nullable]
        # Convention 4: no stored averages.
        assert not [
            c.name
            for c in table.columns
            if c.name.startswith("avg_") or c.name in {"aov", "average"}
        ], "store sum + count and divide at query time"

        declared = {ix.name for ix in table.indexes}
        present = {ix["name"] for ix in inspector.get_indexes(TABLE_NAME)}
        assert not declared - present, f"indexes missing from DB: {declared - present}"

        assert TABLE_NAME in Base.metadata.tables, (
            "app/db/base.py must import this model or alembic autogenerate would "
            "propose dropping the table"
        )
    finally:
        db.close()


def test_twin_sql_matches_the_alembic_migration() -> None:
    """The hand-applied production DDL builds the same table as the revision.

    Production applies reviewed SQL and never runs alembic (DEPLOY.md §6), so
    these two artifacts are independent and can drift — and when they do, CI
    stays green while every analytics call on the live store 500s. So the twin is
    executed here against a SHADOW table and both are reflected and compared
    structurally: same columns, same types, same nullability, same defaults, same
    index and unique-key shapes.
    """
    statements = _twin_statements()
    assert statements, f"{TWIN_SQL.name} contains no DDL"
    assert not any("alembic_version" in s for s in statements), (
        "the generated alembic_version stamp must be stripped: the shared remote "
        "DB is on a lineage this repo does not contain, and stamping it with a "
        "revision id from this chain would corrupt its migration state"
    )

    shadow = f"twin_{TABLE_NAME}"
    db = SessionLocal()
    try:
        db.execute(text(f"DROP TABLE IF EXISTS {shadow}"))
        db.commit()
        for statement in statements:
            db.execute(text(statement.replace(TABLE_NAME, shadow)))
        db.commit()

        inspector = inspect(db.get_bind())
        real = _reflect(inspector, TABLE_NAME)
        twin = _reflect(inspector, shadow)
        assert twin["columns"] == real["columns"], (
            "the twin SQL and the migration disagree about columns:\n"
            f"  twin: {twin['columns']}\n  migration: {real['columns']}"
        )
        assert twin["indexes"] == real["indexes"], (
            f"index shapes differ: twin={twin['indexes']} migration={real['indexes']}"
        )
        assert twin["unique"] == real["unique"], (
            f"unique keys differ: twin={twin['unique']} migration={real['unique']}"
        )
        assert twin["pk"] == real["pk"] == ["id"]
    finally:
        with SessionLocal() as session:
            session.execute(text(f"DROP TABLE IF EXISTS {shadow}"))
            session.commit()
        db.close()


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _family(rendered: str) -> str:
    """Type family, not exact rendering.

    MySQL reports DECIMAL where SQLAlchemy says NUMERIC and renders integer
    widths differently across versions; a family mismatch (NUMERIC modelled,
    DOUBLE stored) is the failure that actually matters. Lifted from
    ``tests/test_analytics_schema.py`` deliberately — two copies of this
    normalisation are better than one shared one that drifts from either suite's
    needs, and BOOL is still checked before INT because MySQL has no boolean.
    """
    t = rendered.upper()
    if "BOOL" in t or "TINYINT" in t:
        return "BOOL"
    for key in ("DECIMAL", "NUMERIC"):
        if key in t:
            return "NUMERIC"
    for key in ("BIGINT", "SMALLINT", "INTEGER", "INT"):
        if key in t:
            return "INT"
    for key in ("VARCHAR", "CHAR", "TEXT"):
        if key in t:
            return "STRING"
    for key in ("DATETIME", "TIMESTAMP"):
        if key in t:
            return "DATETIME"
    if "DATE" in t:
        return "DATE"
    if "FLOAT" in t or "DOUBLE" in t or "REAL" in t:
        return "FLOAT"
    return t


def _unique_constraint(table) -> list[str]:
    from sqlalchemy import UniqueConstraint

    constraints = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    assert len(constraints) == 1, (
        f"expected exactly one UNIQUE key on {table.name}, got {len(constraints)} — "
        "that constraint IS the idempotency key the job upserts against"
    )
    return [c.name for c in constraints[0].columns]


def _twin_statements() -> list[str]:
    """The DDL in the twin file, comments and blank lines stripped."""
    body = "\n".join(
        line
        for line in TWIN_SQL.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("--")
    )
    return [s.strip() for s in body.split(";") if s.strip()]


def _reflect(inspector, name: str) -> dict:
    """A table's structure, with names normalised away.

    The shadow table's identifiers carry a prefix, so everything is compared by
    shape: column name -> (type family, nullable, default), and index/unique keys
    by their column tuples.
    """
    columns = {
        c["name"]: (
            _family(str(c["type"])),
            bool(c["nullable"]),
            _normalise_default(c.get("default")),
        )
        for c in inspector.get_columns(name)
    }
    indexes = sorted(
        tuple(ix["column_names"])
        for ix in inspector.get_indexes(name)
        if not ix.get("unique")
    )
    unique = sorted(
        tuple(ix["column_names"])
        for ix in inspector.get_indexes(name)
        if ix.get("unique")
    ) + sorted(
        tuple(c["column_names"]) for c in inspector.get_unique_constraints(name)
    )
    pk = list(inspector.get_pk_constraint(name)["constrained_columns"])
    return {"columns": columns, "indexes": indexes, "unique": sorted(set(unique)), "pk": pk}


def _normalise_default(default) -> str | None:
    """MySQL quotes and cases server defaults inconsistently across versions."""
    if default is None:
        return None
    return re.sub(r"[\"'()]", "", str(default)).strip().lower()


if __name__ == "__main__":  # pragma: no cover - convenience only
    raise SystemExit(pytest.main([__file__, "-q"]))
