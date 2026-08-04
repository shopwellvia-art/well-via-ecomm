import re
from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field, field_validator, model_validator
from app.schemas.base import AppSchema

# Indian HSN codes are 4, 6 or 8 digits — nothing else is a legal granularity,
# so 5 and 7 are rejected rather than padded. `[0-9]` rather than `\d`: Python's
# `\d` (and `str.isdigit`) also match non-ASCII digits like "٤" and "²", which
# would sail through validation and then be unusable on a GST return.
_HSN_RE = re.compile(r"^(?:[0-9]{4}|[0-9]{6}|[0-9]{8})$")

_HSN_MESSAGE = (
    "hsn_code must be exactly 4, 6 or 8 digits (0-9 only, no spaces, letters "
    "or punctuation). Send null to leave a product unclassified — a blank "
    "string is not a classification."
)


def _validate_hsn_code(value: str | None) -> str | None:
    """Whitespace-trim and reject anything that is not a legal HSN code.

    Deliberately does NOT pad or coerce: a 5-digit value is not a 6-digit code
    with a missing digit, and guessing which digit is missing would put an
    invented tariff heading on a GST invoice. Deliberately does not treat an
    empty string as "unset" either — unlike `brand`, a cleared compliance field
    is worth an error rather than a silent NULL. The admin form already sends
    null for a blank input, so an empty string here means a hand-built request.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(_HSN_MESSAGE)
    code = value.strip()
    if not _HSN_RE.match(code):
        raise ValueError(_HSN_MESSAGE)
    return code


def _normalize_brand(value: str | None) -> str | None:
    """Trim, and collapse an empty/whitespace-only brand to NULL.

    "No brand" must be exactly one representable value. If both NULL and ""
    could reach the column, every brand-dimension rollup would need to know
    about both, and one of them would eventually be forgotten.
    """
    if not isinstance(value, str):
        return value
    return value.strip() or None


class ProductImageRead(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    position: int
    is_primary: bool


class ProductImageReorder(AppSchema):
    """New gallery order — every image id of the product, exactly once."""

    image_ids: list[int] = Field(min_length=1)


class ProductTaxBrief(AppSchema):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    rate: Decimal


class ProductBase(AppSchema):
    sku: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    price: Decimal = Field(ge=0, decimal_places=2)
    # When present, must be strictly greater than `price` — otherwise there's
    # no "discount" to show and the data is misleading.
    compare_at_price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    # Unit procurement / production cost. Used for contribution-margin analytics.
    # Nullable so existing products without a recorded cost stay valid.
    cost: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    stock: int = Field(ge=0, default=0)
    # Optional shipping weight in grams. Drives the rate calculator; null
    # falls back to a 200g default at quote time.
    weight_grams: int | None = Field(default=None, ge=0, le=200_000)
    # When true, any cart containing this product disables COD at checkout
    # (high-value or fragile items where RTO would be unacceptable).
    cod_blocked: bool = False
    image_url: str | None = None
    category_id: int | None = None
    # ── Storefront merchandising (all optional; see Product model for the
    # JSON section contracts) ──
    flavour: str | None = Field(default=None, max_length=80)
    is_combo: bool = False
    offer_text: str | None = Field(default=None, max_length=160)
    coupon_code: str | None = Field(default=None, max_length=40)
    coupon_hint: str | None = Field(default=None, max_length=160)
    short_description: str | None = None
    badge: str | None = Field(default=None, max_length=60)
    sold_count: int | None = Field(default=None, ge=0)
    ingredients: str | None = None
    care_instructions: str | None = None
    highlights: list | None = None
    benefits: list | None = None
    specifications: dict | None = None
    box_contents: list | None = None
    usage_steps: list | None = None
    faqs: list | None = None
    # ── Compliance / merchandising (safe to serve publicly) ──
    # 4/6/8-digit Indian HSN code — see `_validate_hsn_code`. Appears on the
    # customer's own tax invoice, so it is not internal.
    hsn_code: str | None = None
    # Free-text brand; trimmed, and "" normalised to NULL by the validator
    # below. max_length matches `analytics_order_line.brand_snapshot`.
    brand: str | None = Field(default=None, max_length=120)

    _check_hsn = field_validator("hsn_code")(_validate_hsn_code)
    _trim_brand = field_validator("brand", mode="before")(_normalize_brand)

    @model_validator(mode="after")
    def _compare_at_must_exceed_price(self) -> "ProductBase":
        if self.compare_at_price is not None and self.compare_at_price <= self.price:
            raise ValueError("compare_at_price must be greater than price")
        return self


class ProductOpsFields(AppSchema):
    """Internal inventory settings. NEVER on a public response model.

    `products.py`'s read routes (`GET /products`, `/{id}`, `/bestsellers`,
    `/related`, `/co-purchased`, `/likely`, `/by-ids/batch`) are unauthenticated
    storefront endpoints. These two fields were on `ProductBase`, which
    `ProductRead` inherits, so they were being served to anonymous traffic —
    contradicting `models/product.py`'s own "None of them is shown to a shopper".

    `reorder_point` is the one that actually costs something: combined with the
    public stock indicator it hands a competitor the per-SKU stocking policy.
    `shelf_life_days` reveals formulation/turnover assumptions. `hsn_code` and
    `brand` stay on `ProductBase` — the first is printed on the customer's own
    invoice and the second is shopper-facing.

    Mixed into the write models (which are permission-gated) and into
    `ProductAdminRead` (served only by the admin read route), never into
    `ProductRead`.
    """

    # Reorder threshold in units. NULL = no reorder point configured; 0 =
    # "reorder at zero stock". `agg_inventory_daily.reorder_gap` stores NULL for
    # exactly this distinction, so 0 is a legal value and NOT a default.
    reorder_point: int | None = Field(default=None, ge=0)
    # Shelf life in days. `gt=0`, not `ge=0`: a zero-day shelf life is not a
    # product, and allowing it would make "expires immediately" collide with
    # the far more common "not tracked" once anyone rounded a NULL to 0.
    shelf_life_days: int | None = Field(default=None, gt=0)


class ProductCreate(ProductBase, ProductOpsFields):
    pass


class ProductUpdate(AppSchema):
    name: str | None = None
    description: str | None = None
    price: Decimal | None = None
    compare_at_price: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    # Cost update mirrors compare_at_price: optional, non-negative, 2dp.
    cost: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    stock: int | None = None
    weight_grams: int | None = Field(default=None, ge=0, le=200_000)
    cod_blocked: bool | None = None
    image_url: str | None = None
    category_id: int | None = None
    flavour: str | None = Field(default=None, max_length=80)
    is_combo: bool | None = None
    offer_text: str | None = Field(default=None, max_length=160)
    coupon_code: str | None = Field(default=None, max_length=40)
    coupon_hint: str | None = Field(default=None, max_length=160)
    short_description: str | None = None
    badge: str | None = Field(default=None, max_length=60)
    sold_count: int | None = Field(default=None, ge=0)
    ingredients: str | None = None
    care_instructions: str | None = None
    highlights: list | None = None
    benefits: list | None = None
    specifications: dict | None = None
    box_contents: list | None = None
    usage_steps: list | None = None
    faqs: list | None = None
    # Same four analytics/compliance fields, same rules. `exclude_unset` in
    # ProductService.update means an omitted key leaves the column alone while
    # an explicit null clears it — which is how a mis-entered HSN code or a
    # retired reorder point gets removed rather than zeroed.
    reorder_point: int | None = Field(default=None, ge=0)
    hsn_code: str | None = None
    brand: str | None = Field(default=None, max_length=120)
    shelf_life_days: int | None = Field(default=None, gt=0)

    _check_hsn = field_validator("hsn_code")(_validate_hsn_code)
    _trim_brand = field_validator("brand", mode="before")(_normalize_brand)


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    images: list[ProductImageRead] = []
    taxes: list[ProductTaxBrief] = []
    # Denormalized rating aggregates — set by ReviewService. Defaults make
    # responses for new products consistent (avg=0, count=0, no histogram).
    rating_avg: Decimal = Decimal("0.00")
    rating_count: int = 0
    rating_distribution: dict[str, int] | None = None
    created_at: datetime
    updated_at: datetime


class ProductAdminRead(ProductRead, ProductOpsFields):
    """`ProductRead` plus the internal ops fields.

    Returned by the permission-gated product routes only — the admin write
    endpoints and `GET /products/{id}/admin`, which the product edit form loads
    from. The form MUST use this rather than the public detail route: it maps a
    missing key to a blank input and then `analyticsPayload` maps blank back to
    null, so loading from a response without these fields would silently erase a
    configured reorder point on the next save.
    """

