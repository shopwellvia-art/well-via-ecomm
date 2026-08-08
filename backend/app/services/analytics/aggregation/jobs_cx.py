"""The customer-experience rollup job: reviews, ratings and contact volume.

Registered here: ``cx_daily`` (``agg_cx_daily``). The two write patterns and
their guard rails are documented in
:mod:`app.services.analytics.aggregation.jobs`; its helpers are imported rather
than restated.

This is **Pattern B — delete and reinsert**. Which products were reviewed on a
day is discovered, not declared, so the key set can shrink: delete the only
review a product ever had, re-run the bucket, and an upsert would write nothing
for that product and leave yesterday's row behind forever — a one-star row on the
"products pulling the average down" table for a review that no longer exists.
The DELETE runs unconditionally, including when the job computes no rows at all,
because "this bucket used to have rows and now has none" is exactly the case an
upsert cannot express.

Three things this job refuses to do
-----------------------------------
**It does not store an average rating.** It writes the five star counts plus
``rating_sum`` / ``rated_reviews``, and the average is a division at query time.
Averaging daily averages into a week weights a three-review day equally with a
three-hundred-review day; the result is wrong and nothing raises. See
``AggCxDaily`` for the full argument.

**It does not invent a ticket.** ``contact_messages`` is a contact form — no
assignee, no reply timestamp, no closure timestamp. So this job writes volume and
the mix of statuses somebody set, and computes no first-response time, no
resolution time and no SLA attainment, because none of them is measurable. A
plausible number here would be indistinguishable from a measured one.

**It does not attribute a message to a product.** Messages land on the
``product_id = 0`` store-wide row (``STORE_WIDE_PRODUCT_ID``) with every review
column at 0, and product rows carry every message column at 0. The two families
are disjoint per row, so summing either one over any window is correct without
the reader having to filter.

Dating, and what "as of" means
------------------------------
Both sources are dated by ``created_at``, bucketed with
:mod:`app.services.analytics.timebox` so a review left at 23:00 IST belongs to
that IST day rather than to the UTC day it happens to fall in. Everything else on
the row — ``is_approved``, ``helpful_count``, ``contact_messages.status`` — is a
**current** state read at computation time, because neither table records when
its state changed. A bucket therefore restates itself when it is recomputed,
which is the same creation-cohort contract ``shipment_daily`` documents.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.models.analytics_cx import (
    MESSAGE_STATUS_COLUMNS,
    RATING_COLUMNS,
    STORE_WIDE_PRODUCT_ID,
    AggCxDaily,
)
from app.models.contact_message import ContactMessage
from app.models.product import Product
from app.models.review import Review
from app.services.analytics.aggregation.base import JobRunResult, register
from app.services.analytics.aggregation.jobs import (
    _bucket_window,
    _cap,
    _dim,
    _utcnow,
)

__all__ = ["CxDailyJob", "CX_INSERT_CHUNK"]

#: Same reasoning as the inventory job's chunk: one INSERT carrying every
#: reviewed product would grow with the catalogue and eventually exceed
#: `max_allowed_packet`.
CX_INSERT_CHUNK = 500

#: Ratings the distribution can represent. `reviews.rating` carries a CHECK for
#: this range, but the shared production database is on a migration lineage this
#: repo does not contain (DEPLOY.md §6), so the job verifies rather than assumes:
#: anything outside is counted as submitted, excluded from `rating_sum` and
#: `rated_reviews`, and named in a warning. Silently folding it into the nearest
#: bucket would move the average by an amount nobody could trace.
_VALID_RATINGS = frozenset(RATING_COLUMNS)


class CxDailyJob:
    """Reviews per product, plus the day's inbound contact volume.

    Pattern B, one transaction owned by the runner: DELETE the bucket, INSERT
    what the sources say now.

    Row families
    ------------
    * one row per product reviewed in the bucket, carrying the review columns;
    * one row at ``product_id = 0`` carrying the message columns, written **only**
      when the bucket had messages. A day with no contact writes no store-wide
      row rather than a row of zeros, so the table stays a record of what
      happened rather than a calendar.

    Both families are written in the same statement and deleted by the same
    bucket-scoped DELETE, so the whole bucket is rebuilt atomically.

    Identity is snapshotted (``sku_snapshot``, ``category_id_snapshot``) at
    computation time from ``products``. A product deleted since is written with
    the ``'-'`` sentinel and a NULL category rather than dropped: the review was
    really left, and this table has no FK precisely so a deleted product cannot
    erase it.
    """

    name = "cx_daily"

    def run(self, db: Session, bucket_date: date, tz_generation: int) -> JobRunResult:
        start, end, _tz = _bucket_window(db, bucket_date)
        warnings: list[str] = []

        products = self._reviews(db, start, end, warnings)
        catalogue = self._catalogue(db, set(products))
        messages = self._messages(db, start, end, warnings)

        computed_at = _utcnow()
        rows: list[dict[str, Any]] = []
        for product_id in sorted(products):
            sku, category_id = catalogue.get(product_id, (_dim(None), None))
            rows.append(
                {
                    "bucket_date": bucket_date,
                    "product_id": product_id,
                    "tz_generation": tz_generation,
                    "computed_at": computed_at,
                    "sku_snapshot": sku,
                    "category_id_snapshot": category_id,
                    **products[product_id],
                    **_empty_message_counts(),
                }
            )

        if messages["messages_received"]:
            # The store-wide row exists only when somebody wrote in. A row of
            # zeros on a quiet day would be indistinguishable from a day the job
            # never ran, and this table's whole point is that the two look
            # different.
            rows.append(
                {
                    "bucket_date": bucket_date,
                    "product_id": STORE_WIDE_PRODUCT_ID,
                    "tz_generation": tz_generation,
                    "computed_at": computed_at,
                    "sku_snapshot": _dim(None),
                    "category_id_snapshot": None,
                    **_empty_review_counts(),
                    **messages,
                }
            )

        # DELETE first, unconditionally — including when `rows` is empty, which
        # is the case an upsert cannot express: a bucket whose only review was
        # deleted must lose its row rather than keep the old numbers.
        deleted = int(
            db.execute(
                delete(AggCxDaily).where(
                    AggCxDaily.bucket_date == bucket_date,
                    AggCxDaily.tz_generation == tz_generation,
                )
            ).rowcount
            or 0
        )
        for index in range(0, len(rows), CX_INSERT_CHUNK):
            db.execute(
                mysql_insert(AggCxDaily).values(rows[index : index + CX_INSERT_CHUNK])
            )

        return JobRunResult(
            rows_written=len(rows), rows_deleted=deleted, warnings=_cap(warnings)
        )

    # -- pieces -----------------------------------------------------------
    @staticmethod
    def _reviews(
        db: Session, start, end, warnings: list[str]
    ) -> dict[int, dict[str, int]]:
        """Per-product review counters for the bucket, keyed by product id.

        One grouped query rather than one per counter: every column here is a
        conditional SUM over the same population, so they are all consistent by
        construction and cost one scan.

        ``rating_sum`` and ``rated_reviews`` are accumulated over the valid range
        ONLY, which is what keeps the average describing the population the five
        buckets describe.
        """
        rating_sums = {
            column: func.coalesce(
                func.sum(case((Review.rating == value, 1), else_=0)), 0
            ).label(column)
            for value, column in RATING_COLUMNS.items()
        }
        in_range = Review.rating.in_(sorted(_VALID_RATINGS))

        rows = db.execute(
            select(
                Review.product_id.label("product_id"),
                func.count(Review.id).label("reviews_submitted"),
                func.coalesce(
                    func.sum(case((Review.is_approved.is_(True), 1), else_=0)), 0
                ).label("reviews_approved"),
                func.coalesce(
                    func.sum(case((Review.is_approved.is_(True), 0), else_=1)), 0
                ).label("reviews_unapproved"),
                func.coalesce(
                    func.sum(case((in_range, Review.rating), else_=0)), 0
                ).label("rating_sum"),
                func.coalesce(func.sum(case((in_range, 1), else_=0)), 0).label(
                    "rated_reviews"
                ),
                func.coalesce(
                    func.sum(
                        case((Review.is_verified_purchase.is_(True), 1), else_=0)
                    ),
                    0,
                ).label("verified_purchase_reviews"),
                func.coalesce(func.sum(Review.helpful_count), 0).label("helpful_votes"),
                *rating_sums.values(),
            )
            .where(Review.created_at >= start, Review.created_at < end)
            .group_by(Review.product_id)
        ).all()

        out: dict[int, dict[str, int]] = {}
        unrated = 0
        for row in rows:
            counters = {
                "reviews_submitted": int(row.reviews_submitted or 0),
                "reviews_approved": int(row.reviews_approved or 0),
                "reviews_unapproved": int(row.reviews_unapproved or 0),
                "rating_sum": int(row.rating_sum or 0),
                "rated_reviews": int(row.rated_reviews or 0),
                "verified_purchase_reviews": int(row.verified_purchase_reviews or 0),
                "helpful_votes": int(row.helpful_votes or 0),
                **{
                    column: int(getattr(row, column) or 0)
                    for column in RATING_COLUMNS.values()
                },
            }
            unrated += counters["reviews_submitted"] - counters["rated_reviews"]
            out[int(row.product_id)] = counters

        if unrated:
            warnings.append(
                f"cx_rating_out_of_range: {unrated} review(s) in this bucket carry a "
                "rating outside 1..5; they are counted in reviews_submitted and "
                "excluded from rating_sum/rated_reviews, so the average describes "
                "the reviews the distribution describes and nothing else"
            )
        return out

    @staticmethod
    def _catalogue(db: Session, product_ids: set[int]) -> dict[int, tuple[str, int | None]]:
        """`(sku_snapshot, category_id_snapshot)` per product, as of right now.

        A product that no longer exists snapshots as the `'-'` sentinel with a
        NULL category rather than being dropped — the review was really left, and
        this table has no FK precisely so a deleted product cannot erase it.
        """
        if not product_ids:
            return {}
        return {
            int(row.id): (_dim(row.sku), row.category_id)
            for row in db.execute(
                select(Product.id, Product.sku, Product.category_id).where(
                    Product.id.in_(product_ids)
                )
            ).all()
        }

    @staticmethod
    def _messages(db: Session, start, end, warnings: list[str]) -> dict[str, int]:
        """Inbound contact volume for the bucket, split by current status.

        ``contact_messages.status`` is a free-form VARCHAR so ops can adopt their
        own vocabulary without a migration. An unrecognised value is counted in
        ``messages_other`` and named in a warning — never folded into
        ``messages_new``, which would make an invented status read as an
        unanswered message.

        The four status columns partition ``messages_received`` exactly, which is
        what lets a status mix be re-aggregated to any window.
        """
        rows = db.execute(
            select(
                ContactMessage.status.label("status"),
                func.count(ContactMessage.id).label("messages"),
            )
            .where(ContactMessage.created_at >= start, ContactMessage.created_at < end)
            .group_by(ContactMessage.status)
        ).all()

        counts: dict[str, int] = defaultdict(int)
        unknown: dict[str, int] = {}
        for row in rows:
            raw = (row.status or "").strip().lower()
            messages = int(row.messages or 0)
            counts["messages_received"] += messages
            column = MESSAGE_STATUS_COLUMNS.get(raw)
            if column is None:
                counts["messages_other"] += messages
                unknown[raw or "(empty)"] = unknown.get(raw or "(empty)", 0) + messages
                continue
            counts[column] += messages

        if unknown:
            listed = ", ".join(f"{status!r}={n}" for status, n in sorted(unknown.items()))
            warnings.append(
                f"cx_unknown_message_status: {listed}. contact_messages.status is "
                "free-form, so these are counted in messages_other rather than as "
                "new or replied; the status mix names them instead of guessing"
            )

        return {"messages_received": counts["messages_received"], **{
            column: counts[column]
            for column in (*MESSAGE_STATUS_COLUMNS.values(), "messages_other")
        }}


def _empty_review_counts() -> dict[str, int]:
    """Zeroed review columns, for the store-wide row. A message has no rating."""
    return {
        "reviews_submitted": 0,
        "reviews_approved": 0,
        "reviews_unapproved": 0,
        "rating_sum": 0,
        "rated_reviews": 0,
        "verified_purchase_reviews": 0,
        "helpful_votes": 0,
        **{column: 0 for column in RATING_COLUMNS.values()},
    }


def _empty_message_counts() -> dict[str, int]:
    """Zeroed message columns, for a product row. A review is not a message."""
    return {
        "messages_received": 0,
        **{
            column: 0
            for column in (*MESSAGE_STATUS_COLUMNS.values(), "messages_other")
        },
    }


register(CxDailyJob())
