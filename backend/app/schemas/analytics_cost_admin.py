"""Request/response models for the cost-rule and marketing-spend admin API.

These types exist to make two rules impossible to get wrong at a call site.

**A rate is versioned, not edited.** There is no `CostRuleUpdate` in this module
and that absence is deliberate. The only way to change a rate is
:class:`CostRuleSupersedeRequest`, which names the date the *new* value takes
effect — because a PATCH that took `{"value": 12}` and no date would be a schema
that invites rewriting March's margin, and no amount of documentation elsewhere
stops a schema that accepts the wrong thing. See
`endpoints/analytics_cost_admin.py` for what supersession actually does.

**A period is a period, not a date.** :class:`MarketingSpendUpsert` takes a
grain and one date inside the period, and normalises: a MONTHLY entry for any
day in June becomes 1–30 June. An admin who picks the 12th from a date picker
means June, and a row stored as 12 June → 12 June would allocate a month's spend
onto one day forever after.

Money is `Decimal`, never `float`. Pydantic will coerce `12.5` from JSON into
`Decimal("12.5")` here; what it must never do is carry a binary float into a
number that gets subtracted from revenue.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Widest span of buckets one write may enqueue for recompute. A rate that took
#: effect two years ago is legitimate; enqueuing 730 days × every cost-consuming
#: job from a single form submission is not. Beyond this the response says
#: exactly which earlier buckets were left alone and how to enqueue them, rather
#: than silently covering some of them.
MAX_RECOMPUTE_DAYS = 400

#: Page size for the two list endpoints. Both tables are small by construction
#: (one row per rate change; one row per channel per period) but "small by
#: construction" is how unbounded list endpoints get shipped.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500


# ===========================================================================
# Cost rules
# ===========================================================================
class CostRuleRead(BaseModel):
    """One `analytics_cost_rules` row.

    `effective_to = null` means "still in force" — an open-ended rule, which is
    the normal state of the current rate. `is_current` is computed rather than
    stored so it cannot go stale: a rule that ended yesterday is not current
    today, and nothing wrote a row to say so.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    cost_type: str
    scope: str
    scope_value: str
    value: Decimal
    unit: str
    currency: str
    quality: str
    effective_from: date
    effective_to: date | None
    source: str | None
    note: str | None
    created_by_user_id: int | None
    created_at: datetime


class CostRuleListResponse(BaseModel):
    """A page of rules, plus the vocabularies the form needs.

    The four constant lists ride along with the list response so the admin UI
    never hardcodes them. `unit` in particular is a 100x error waiting to happen
    — 2.5 is either 2.5% or ₹2.50 — and a front end guessing at the valid set is
    how a typo becomes a wrong margin instead of a 422.
    """

    rules: list[CostRuleRead]
    total: int
    cost_types: list[str]
    scopes: list[str]
    units: list[str]
    qualities: list[str]


class _CostRuleFieldsMixin(BaseModel):
    """Fields shared by create and supersede, so the two cannot drift apart."""

    value: Decimal = Field(
        max_digits=14,
        decimal_places=4,
        description=(
            "The rate or amount. Meaningless without `unit`: for PCT this is a "
            "percentage (2.5 means 2.5%, NOT 0.025); for the money units it is "
            "an amount in `currency`."
        ),
    )
    unit: str = Field(
        max_length=24,
        description="CostUnit: pct | per_order | per_unit | per_kg | per_month.",
    )
    quality: str = Field(
        max_length=16,
        description=(
            "CostQuality: actual | contracted | estimated | assumed. Rides "
            "through to the metric quality label, so a margin built on a typed "
            "guess is never presented like one built on a settled invoice."
        ),
    )
    currency: str = Field(
        default="INR", min_length=3, max_length=3, description="ISO-4217. Ignored for PCT."
    )
    effective_from: date = Field(
        description=(
            "First store-local reporting day this value applies to. Buckets "
            "from this day onward are enqueued for recompute."
        )
    )
    effective_to: date | None = Field(
        default=None,
        description=(
            "Last day this value applies to, inclusive. Null means open-ended / "
            "still in force, which is the normal case for a current rate."
        ),
    )
    source: str | None = Field(
        default=None,
        max_length=64,
        description="Where the number came from: 'razorpay_settlement_2026_03'.",
    )
    note: str | None = Field(default=None, max_length=255)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _validate_window(self) -> "_CostRuleFieldsMixin":
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError(
                "effective_to must be on or after effective_from; the window is "
                "inclusive on both ends."
            )
        return self


class CostRuleCreate(_CostRuleFieldsMixin):
    """Insert a rule for a (cost_type, scope, scope_value) window.

    This is for a rate that does not exist yet, or one that applies to a window
    no existing rule covers. Changing an existing rate goes through
    `POST /admin/cost-rules/{id}/supersede` instead — an insert whose window
    overlaps a live rule is rejected with 409 rather than silently becoming
    whichever row the resolver's ORDER BY happens to reach first.
    """

    cost_type: str = Field(
        max_length=48,
        description="CostType: gateway_fee, packaging, marketing_spend, ...",
    )
    scope: str = Field(
        default="global",
        max_length=24,
        description="CostScope: global | gateway | courier | category | product | payment_method.",
    )
    scope_value: str = Field(
        default="-",
        max_length=64,
        description=(
            "The scoped entity. '-' for a global rule — NOT NULL sentinel, the "
            "UNIQUE key spans this column."
        ),
    )


class CostRuleSupersedeRequest(_CostRuleFieldsMixin):
    """Change a rate from a given day, keeping every earlier report reproducible.

    `cost_type`, `scope` and `scope_value` are NOT accepted: a supersession is
    the same rate line continuing with a new value, and letting the caller move
    it to a different scope would silently orphan the old row's coverage. To
    point a rule at a different scope, close it and create a new one.

    `effective_from` must be strictly after the superseded rule's own
    `effective_from`. Equal would mean the old rule covered no days at all, which
    is an edit disguised as a version.
    """


class CostRuleWriteResponse(BaseModel):
    """201/200 body for a create or supersede.

    `recompute` is not decoration. A rate change that did not enqueue anything
    has changed nothing an admin can see, and the numbers on the dashboard stay
    stale until the next unrelated recompute happens to cover them — a silent
    outcome that looks identical to success. Surfacing the queued window and the
    row count is what makes "did my change take effect?" answerable.
    """

    rule: CostRuleRead
    superseded_rule: CostRuleRead | None = Field(
        default=None,
        description=(
            "The row that was closed to make room for this one, with its new "
            "`effective_to`. Null for a plain create. Its `value` is unchanged — "
            "reports for dates inside its window still resolve the old rate."
        ),
    )
    recompute_queued: int = Field(
        description="Queue rows created or reopened across all affected jobs."
    )
    recompute_jobs: list[str] = Field(default_factory=list)
    recompute_from: date | None = None
    recompute_to: date | None = None
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal things the operator must know — most importantly, buckets "
            "older than the recompute cap that were deliberately NOT enqueued."
        ),
    )


# ===========================================================================
# Marketing spend
# ===========================================================================
class MarketingSpendRead(BaseModel):
    """One `analytics_marketing_spend` row, as stored."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    grain: str
    period_start: date
    period_end: date
    channel: str
    campaign: str
    amount: Decimal
    currency: str
    quality: str
    source: str
    note: str | None
    entered_by_user_id: int | None
    tz_generation: int
    created_at: datetime
    updated_at: datetime


class MarketingSpendUpsert(BaseModel):
    """Record what was spent on a channel in a period.

    Idempotent on `(grain, period_start, channel, campaign)`: submitting June/Meta
    twice replaces the figure rather than adding to it. That is what makes a
    double-clicked form harmless and what will let an ads API later overwrite a
    human's estimate with the platform's own number.

    `period` is any day inside the period. For MONTHLY it is normalised to the
    whole calendar month server-side — see the module docstring.
    """

    grain: str = Field(
        default="daily", description="SpendGrain: daily | monthly."
    )
    period: date = Field(
        description=(
            "Any store-local day inside the period. For grain=daily this IS the "
            "day; for grain=monthly the containing month is used."
        )
    )
    channel: str = Field(
        default="-",
        max_length=48,
        description="'meta', 'google', ... '-' means all marketing, unattributed.",
    )
    campaign: str = Field(
        default="-",
        max_length=96,
        description="'-' means the channel total rather than a specific campaign.",
    )
    amount: Decimal = Field(
        ge=0,
        max_digits=14,
        decimal_places=2,
        description=(
            "Money spent in the period, in `currency`. Required — an omitted "
            "amount is not zero, and zero spend reported as fact would make "
            "acquisition look free."
        ),
    )
    currency: str = Field(default="INR", min_length=3, max_length=3)
    quality: str = Field(
        default="assumed",
        max_length=16,
        description="SpendQuality: actual | contracted | estimated | assumed.",
    )
    source: str = Field(
        default="manual",
        max_length=64,
        description=(
            "'manual' for a figure a human typed, or an integration id for one "
            "an API delivered. This is what keeps 'we think' distinguishable "
            "from 'we know' after the fact."
        ),
    )
    note: str | None = Field(default=None, max_length=255)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


class SpendDayAllocation(BaseModel):
    """One store-local day's share of a spend row, for a daily view.

    `quality` is ALLOCATED for every day derived from a monthly lump, whatever
    the row's own quality says. A monthly invoice is excellent evidence about
    *June*; it is an inference about the 12th, and the label has to say which of
    those a reader is looking at.
    """

    day: date
    channel: str
    campaign: str
    amount: Decimal
    currency: str
    grain: str
    quality: str = Field(
        description="MetricQuality: ACTUAL | ESTIMATED for daily rows, ALLOCATED for spread ones."
    )
    spend_id: int


class MarketingSpendListResponse(BaseModel):
    """A page of spend rows, optionally with the daily allocation preview.

    `daily` is populated only when the caller asks for it, because computing it
    means expanding every monthly row in the window into days and that is a
    different question from "what has been entered?".
    """

    spend: list[MarketingSpendRead]
    total: int
    grains: list[str]
    qualities: list[str]
    daily: list[SpendDayAllocation] = Field(default_factory=list)
    daily_total: Decimal | None = Field(
        default=None,
        description=(
            "Sum of `daily`. Equal to the sum of the underlying rows' amounts "
            "for any window that fully contains them — allocation redistributes "
            "money, it never creates or loses any."
        ),
    )


class MarketingSpendWriteResponse(BaseModel):
    """201/200 body for a spend upsert."""

    spend: MarketingSpendRead
    created: bool = Field(
        description="True for a new row, False when an existing period was replaced."
    )
    previous_amount: Decimal | None = Field(
        default=None, description="What the replaced row said, for the audit trail."
    )
    recompute_queued: int
    recompute_jobs: list[str] = Field(default_factory=list)
    recompute_from: date | None = None
    recompute_to: date | None = None
    warnings: list[str] = Field(default_factory=list)
