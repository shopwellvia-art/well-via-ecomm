from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from app.models.loyalty import PointsReason
from app.schemas.base import AppSchema


class PointsTransactionRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    delta: int
    reason: PointsReason
    ref_type: str | None
    ref_id: int | None
    description: str | None
    created_at: datetime


class TransactionListPage(AppSchema):
    items: list[PointsTransactionRead]
    total: int
    page: int
    page_size: int


class RedemptionTierRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cost_points: int
    discount_type: str
    discount_value: Decimal
    max_discount: Decimal | None
    expires_after_days: int
    is_active: bool


class RedemptionTierCreate(AppSchema):
    name: str = Field(min_length=2, max_length=120)
    cost_points: int = Field(ge=1)
    discount_type: str = Field(pattern="^(percent|fixed)$")
    discount_value: Decimal = Field(gt=0)
    max_discount: Decimal | None = Field(default=None, ge=0)
    expires_after_days: int = Field(default=30, ge=1, le=365)
    is_active: bool = True


class RedemptionTierUpdate(AppSchema):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    cost_points: int | None = Field(default=None, ge=1)
    discount_type: str | None = Field(default=None, pattern="^(percent|fixed)$")
    discount_value: Decimal | None = Field(default=None, gt=0)
    max_discount: Decimal | None = Field(default=None, ge=0)
    expires_after_days: int | None = Field(default=None, ge=1, le=365)
    is_active: bool | None = None


class VipTierRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    threshold_lifetime_points: int
    earn_multiplier: Decimal
    benefits: str | None
    color: str | None
    sort_order: int


class VipTierCreate(AppSchema):
    name: str = Field(min_length=2, max_length=50)
    threshold_lifetime_points: int = Field(ge=0)
    earn_multiplier: Decimal = Field(ge=Decimal("1.00"), le=Decimal("10.00"))
    benefits: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=16)
    sort_order: int = 0


class VipTierUpdate(AppSchema):
    name: str | None = Field(default=None, min_length=2, max_length=50)
    threshold_lifetime_points: int | None = Field(default=None, ge=0)
    earn_multiplier: Decimal | None = Field(default=None, ge=Decimal("1.00"), le=Decimal("10.00"))
    benefits: str | None = Field(default=None, max_length=500)
    color: str | None = Field(default=None, max_length=16)
    sort_order: int | None = None


class TierProgress(AppSchema):
    """Storefront helper — the customer's current tier plus how close they are
    to the next one. `progress_pct` is 0–100 and `points_to_next` is the gap
    (null when already at the top)."""

    current: VipTierRead | None = None
    next: VipTierRead | None = None
    points_to_next: int | None = None
    progress_pct: int = 0


class EarnRuleRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key: str
    display_name: str
    description: str | None
    points_value: int
    is_active: bool


class EarnRuleUpdate(AppSchema):
    """Only value + active flag are tunable. The `key` is the machine ID and
    the display label is fixed in the seed."""

    points_value: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class LoyaltySummary(AppSchema):
    """The /loyalty/me payload — everything the rewards page needs in one hit."""

    balance: int
    lifetime: int
    tiers: list[RedemptionTierRead]
    recent: list[PointsTransactionRead]
    tier_progress: TierProgress = TierProgress()


class RedeemRequest(AppSchema):
    tier_id: int


class RedeemResponse(AppSchema):
    """Whatever the customer needs to actually use the reward at checkout."""

    coupon_code: str
    discount_type: str
    discount_value: Decimal
    expires_at: datetime | None
    new_balance: int


class AdminAdjustRequest(AppSchema):
    delta: int = Field(description="Positive = credit, negative = debit. Cannot be zero.")
    description: str = Field(min_length=3, max_length=200)


class AdminUserLoyaltyRead(AppSchema):
    """What the admin sees when they look up a single customer's loyalty record."""

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    full_name: str | None
    balance: int
    lifetime: int
    transactions: list[PointsTransactionRead]
