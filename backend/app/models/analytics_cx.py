"""The customer-experience rollup: reviews, ratings and inbound contact volume.

One table, ``agg_cx_daily``, built to the conventions in
:mod:`app.models.analytics_base` and :mod:`app.models.analytics_rollups` — which
are not restated here because they are not negotiable: BigInteger PK, no foreign
keys, ``dimension_column()`` for anything in the UNIQUE key, ``tz_generation`` in
that key, and **sums and counts, never a stored average**.

It lives in its own module rather than in ``analytics_rollups.py`` because it is
sourced from ``reviews`` and ``contact_messages`` — the customer-experience
surface — and nothing in it participates in the revenue bridge. The schema test
collects rollups from ``analytics_rollups``; this table is covered by
``tests/test_analytics_cx_rollup.py``, which asserts the same four conventions
against it plus the two that only apply here.

Why the ratings are FIVE COUNTS and not an average
--------------------------------------------------
An average cannot be re-bucketed. Averaging seven daily ``avg_rating`` values
into a week weights a day with three reviews the same as a day with three
hundred, and the answer is wrong *silently* — nothing raises, and the number
stays plausible. So this table stores ``rating_1`` .. ``rating_5``, and the
average is ``rating_sum / rated_reviews`` computed at query time over whatever
window is asked for. The five counts are strictly more information than a mean:
they give the mean back exactly, plus the distribution, plus the answer to "how
many people gave this one star", which is the question a merchant actually asks.

``rating_sum`` is the additive half of that pair, exactly like
``sum_settle_seconds`` / ``n_settle`` on ``agg_payment_daily``. It is redundant
with the five counts by construction — ``1*rating_1 + ... + 5*rating_5`` — and a
test asserts that identity on every written row, so the redundancy is checkable
rather than a second source of truth. It is stored because the query layer sums
columns and cannot apply per-column weights, and because the sum+count pair is
the shape every other duration/average in this schema already uses.

Two grains in one table, and the sentinel that keeps them apart
--------------------------------------------------------------
Reviews are per product. A contact message is not: ``contact_messages`` has no
product, no order and no ticket. Both are the same measurement — "what customers
told us on this day" — and both belong to this view module, so they share the
table and are kept apart by ``product_id``:

* ``product_id > 0`` — one row per product that received a review that day. Every
  ``messages_*`` column is 0 on these rows.
* ``product_id = 0`` (:data:`STORE_WIDE_PRODUCT_ID`) — the store-wide row,
  written only on days with inbound messages. Every review column is 0 on it.

Because the two column families are disjoint per row, ``SUM(messages_received)``
over a day, a week or the whole table is the true message count and
``SUM(reviews_submitted)`` is the true review count, with no double counting and
no filtering required by the reader. 0 is used rather than the ``'-'`` sentinel
because ``product_id`` is an integer: MySQL AUTO_INCREMENT never issues 0, so it
cannot collide with a real product, and a NULL would defeat the UNIQUE key on
MySQL exactly the way a NULL dimension does.

What this table cannot say, and must never pretend to
-----------------------------------------------------
**No ticketing.** ``contact_messages`` is a contact form: name, email, subject,
message and a free-form ``status`` string. There is no assignee, no thread, no
first-reply timestamp and no closure timestamp anywhere in the schema. So there
is no first-response time, no resolution time, no SLA attainment and no backlog
age here, and columns named ``sum_first_response_seconds``, ``resolution_time``,
``sla_met`` or ``tickets_open`` are **forbidden** — the same rule, for the same
reason, that ``AggShipmentDaily`` forbids ``on_time``. Any such number would be
measured against a clock this system never started. The honest substitute is
what is here: volume, and the mix of the statuses somebody actually set.

**No rejected reviews.** ``reviews.is_approved`` is one boolean with no audit
trail, so a review that a moderator rejected and a review nobody has looked at
yet are the same row. ``reviews_unapproved`` is named for what it can prove.
Calling it ``reviews_rejected`` would invent a moderation decision.

**Status and approval are CURRENT states.** Neither table records when its state
changed, so both are read as of the moment the bucket was computed — this is a
creation cohort, like ``AggShipmentDaily``: the population is what was created in
the bucket, and its state is today's. A bucket therefore restates itself when it
is recomputed, which is what the recompute queue is for.

**``helpful_votes`` is a level read as a flow, and it is the weakest column
here.** ``reviews.helpful_count`` is a mutable counter with no event log, so this
is "helpful votes standing on the reviews *created* that day, as of computation",
not "votes cast that day". It is additive across days only because each review
belongs to exactly one bucket. A vote cast today on a review from March raises
March's number, not today's.
"""
from __future__ import annotations

from sqlalchemy import Index, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.analytics_base import (
    BigIDMixin,
    RollupMixin,
    count_column,
    dimension_column,
)
from app.models.base import Base

__all__ = ["AggCxDaily", "STORE_WIDE_PRODUCT_ID", "RATING_COLUMNS", "MESSAGE_STATUS_COLUMNS"]

#: ``product_id`` of the store-wide row that carries the contact-message
#: counters. Not a real product: MySQL AUTO_INCREMENT starts at 1, so 0 can never
#: collide with one, and unlike NULL it does not defeat the UNIQUE key.
STORE_WIDE_PRODUCT_ID = 0

#: star value -> the column counting it. Iterated by the job and by the tests, so
#: the distribution and its checks cannot drift apart.
RATING_COLUMNS: dict[int, str] = {
    1: "rating_1",
    2: "rating_2",
    3: "rating_3",
    4: "rating_4",
    5: "rating_5",
}

#: ``contact_messages.status`` (lower-cased) -> the column counting it. The
#: column is a free-form VARCHAR so ops can adopt their own vocabulary without a
#: migration; anything outside this map is counted in ``messages_other`` and
#: named in a run warning rather than dropped.
MESSAGE_STATUS_COLUMNS: dict[str, str] = {
    "new": "messages_new",
    "replied": "messages_replied",
    "closed": "messages_closed",
}


class AggCxDaily(Base, BigIDMixin, RollupMixin):
    """Reviews per product per day, plus the day's inbound contact volume.

    Grain is ``(bucket_date, product_id, tz_generation)`` — see the module
    docstring for the ``product_id = 0`` store-wide row, which is the only place
    the ``messages_*`` columns are ever non-zero.

    Answers: how many reviews came in, how they were rated, which products are
    collecting one-star reviews, how much of the feedback comes from verified
    buyers, and how many people wrote in.

    ``product_id`` has no FK, so deleting a product never erases the reviews it
    received; identity is snapshotted into the row instead
    (``sku_snapshot``, ``category_id_snapshot``) so a rename or a
    re-categorisation tomorrow does not rewrite last quarter's feedback. This
    mirrors ``AggProductDaily`` exactly, and deliberately: the two are joined by
    eye on a product page, and a different snapshot rule would make them disagree
    about what a product was called.

    **Every rate here is a pair, never a stored percentage.** Approval rate is
    ``reviews_approved / reviews_submitted``; verified-purchase share is
    ``verified_purchase_reviews / reviews_submitted``; the average rating is
    ``rating_sum / rated_reviews``. All four denominators are stored, so any of
    them can be re-aggregated to a week, a month or a category correctly.

    ``rated_reviews`` is a separate denominator from ``reviews_submitted`` on
    purpose. Only a review whose rating lands in 1..5 contributes to
    ``rating_sum`` and to the five buckets; anything outside that range (which a
    CHECK constraint should prevent, and which the job warns about if it ever
    appears) is counted as submitted and excluded from the average, so the
    average is never quietly computed over a population it does not describe.
    ``rating_1 + ... + rating_5 == rated_reviews`` holds on every row.
    """

    __tablename__ = "agg_cx_daily"

    #: The product reviewed. :data:`STORE_WIDE_PRODUCT_ID` (0) is the store-wide
    #: row carrying the contact-message counters — a message has no product.
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The SKU as it was on `bucket_date`; `'-'` on the store-wide row and for a
    #: product that has since been deleted. History must not follow a rename.
    sku_snapshot: Mapped[str] = dimension_column(64)
    #: The category as it was on `bucket_date`. NULL means uncategorised (or the
    #: store-wide row); not part of the UNIQUE key, so the nullability is safe.
    category_id_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---- Review volume and moderation state ------------------------------
    #: Every review created in the bucket, whatever its moderation state.
    #: The denominator for approval rate and verified-purchase share.
    reviews_submitted: Mapped[int] = count_column()
    #: `is_approved` as of computation — a current state, not an event.
    reviews_approved: Mapped[int] = count_column()
    #: NOT `reviews_rejected`. One boolean cannot tell "a moderator said no" from
    #: "nobody has looked yet"; see the module docstring.
    reviews_unapproved: Mapped[int] = count_column()

    # ---- Rating distribution ---------------------------------------------
    # Five counts, never an average. See the module docstring for why.
    rating_1: Mapped[int] = count_column()
    rating_2: Mapped[int] = count_column()
    rating_3: Mapped[int] = count_column()
    rating_4: Mapped[int] = count_column()
    rating_5: Mapped[int] = count_column()

    #: Sum of the stars given. The additive half of the average; divide by
    #: `rated_reviews`, never by `reviews_submitted`. Equals
    #: `1*rating_1 + ... + 5*rating_5` by construction.
    rating_sum: Mapped[int] = count_column()
    #: Reviews whose rating was inside 1..5 and therefore contributed to
    #: `rating_sum` and to the five buckets. The average's own denominator.
    rated_reviews: Mapped[int] = count_column()

    # ---- Trust and engagement ---------------------------------------------
    #: Numerator for verified-purchase share. Never store the share.
    verified_purchase_reviews: Mapped[int] = count_column()
    #: Helpful votes standing on the reviews created in this bucket, as of
    #: computation. See the module docstring: this is not "votes cast today".
    helpful_votes: Mapped[int] = count_column()

    # ---- Inbound contact volume (store-wide row only) ---------------------
    #: Every contact message received in the bucket. The status columns below
    #: partition it exactly: new + replied + closed + other == received.
    messages_received: Mapped[int] = count_column()
    messages_new: Mapped[int] = count_column()
    messages_replied: Mapped[int] = count_column()
    messages_closed: Mapped[int] = count_column()
    #: Messages whose free-form status is outside the known vocabulary. Kept as
    #: its own column rather than folded into `messages_new` so an ops team that
    #: invents a status shows up as unclassified instead of as unanswered.
    messages_other: Mapped[int] = count_column()

    __table_args__ = (
        UniqueConstraint(
            "bucket_date", "product_id", "tz_generation", name="uq_agg_cx_daily_key"
        ),
        Index("ix_agg_cx_daily_product_bucket_date", "product_id", "bucket_date"),
        Index("ix_agg_cx_daily_bucket_date_category", "bucket_date", "category_id_snapshot"),
    )
