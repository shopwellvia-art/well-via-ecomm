from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.review import Review
    from app.models.tax import Tax


class Category(Base, IDMixin, TimestampMixin):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(140), unique=True, index=True, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(512))
    # One level of nesting only: a parent must itself be top-level. Enforced in
    # CategoryService; the self-referential FK is the integrity safety net.
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)

    parent: Mapped[Category | None] = relationship(
        back_populates="children", remote_side="Category.id"
    )
    children: Mapped[list["Category"]] = relationship(back_populates="parent")

    products: Mapped[list["Product"]] = relationship(back_populates="category")


class Product(Base, IDMixin, TimestampMixin):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # When set and greater than `price`, the storefront renders this as a
    # strikethrough "compare at" / "was" price next to a Sale badge.
    compare_at_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # What we paid to buy/make one unit. Used for contribution-margin / profit
    # analytics. Nullable: existing products have no cost recorded yet.
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    stock: Mapped[int] = mapped_column(default=0, nullable=False)
    # Shipping weight in grams. Null falls back to a 200g default at rate-quote
    # time so untagged SKUs still ship. Admin-editable from the product form.
    weight_grams: Mapped[int | None] = mapped_column(Integer)
    # Any cart containing a cod_blocked product disables the COD option at
    # checkout. Used for high-value or fragile SKUs where RTO is unacceptable.
    cod_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Denormalized primary image — kept in sync with the primary ProductImage
    # so listing/card queries stay cheap.
    image_url: Mapped[str | None] = mapped_column(String(512))

    # ── Analytics & compliance attributes ──
    # Four capture-only fields. None of them is shown to a shopper; each exists
    # because a reporting column downstream is already built to consume it and
    # is currently blank forever. All four are NULLABLE with NO server default,
    # for the same reason `analytics_marketing_spend.amount` is: a product that
    # predates the column has genuinely unknown values, and a default would
    # assert a fact nobody entered.
    #
    # Reorder threshold in units. `agg_inventory_daily.reorder_gap` is
    # `stock_close - reorder_point` and is NULL on every row today precisely
    # because no product carries one. NULL here means "no reorder point
    # configured"; 0 means "reorder only once the shelf is empty". Those are
    # different statements and the rollup depends on telling them apart, which
    # is why this must never be defaulted to 0.
    reorder_point: Mapped[int | None] = mapped_column(Integer)
    # Indian HSN code — 4, 6 or 8 digits, the granularity a GST invoice and a
    # GSTR filing are keyed on. Stored as a STRING, not an INT: leading zeros
    # are significant in the HSN tariff and an integer column would silently
    # eat them. Width is 8 because 8 digits is the longest legal code, so an
    # over-long value fails loudly at the column rather than being truncated.
    # NULL = not classified yet; view 70 (Tax and GST) must keep declaring
    # itself NOT compliance-grade until coverage exists.
    hsn_code: Mapped[str | None] = mapped_column(String(8))
    # Manufacturer / house brand. Width matches
    # `analytics_order_line.brand_snapshot` (String(120)) exactly — that column
    # was reserved for this field and snapshots it at order time, so a wider
    # value here would truncate on the way into the fact table. Empty string is
    # normalised to NULL in the schema layer so "no brand" is one value, not
    # two indistinguishable ones. Not indexed: the brand dimension is queried
    # off the fact table's snapshot, never off this row.
    brand: Mapped[str | None] = mapped_column(String(120))
    # Shelf life in days. The `days_of_inventory` KPI carries the caveat that
    # "shelf life is not modelled; a supplement can have plenty of cover and
    # still expire before it sells" — this is the column that answers it.
    # NULL = not tracked, which is most of the catalog and must stay
    # distinguishable from a short-dated product. 0 is rejected upstream: a
    # zero-day shelf life is not a sellable product, it is a typo.
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)

    # ── Storefront merchandising fields (all optional) ──
    # Flavour tag shown on cards/PDP and used by the listing flavour filter.
    flavour: Mapped[str | None] = mapped_column(String(80), index=True)
    # Combo/bundle SKUs are merchandised separately (Categories "Combo Packs"
    # tab, Offers filter). A flag, not a bundle model — combos are ordinary
    # SKUs with their own price/stock.
    is_combo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # Promotional chip next to the PDP price, e.g. "Buy 1 Get 1 free!".
    offer_text: Mapped[str | None] = mapped_column(String(160))
    # Copyable coupon promoted on the PDP ("Use Code: WELLVIA") with its
    # explanatory hint line ("Get it for ₹800"). Purely presentational — the
    # actual discount is validated by the coupon engine at cart/checkout.
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    coupon_hint: Mapped[str | None] = mapped_column(String(160))

    # ── Rich PDP content ──
    # NOTE: these columns already exist on the shared remote MySQL (added by a
    # newer migration lineage stamped `conpay001` that is not in this repo).
    # They are mapped here so the ORM matches the live schema; the repo's own
    # migration adds them conditionally for fresh local databases.
    # One-liner under the title on cards/wishlist rows.
    short_description: Mapped[str | None] = mapped_column(Text)
    # Free-form card ribbon ("Bestseller", "New", …). Overrides derived badges.
    badge: Mapped[str | None] = mapped_column(String(60))
    # Denormalized lifetime units sold; alternative bestseller signal.
    sold_count: Mapped[int | None] = mapped_column(Integer)
    # Plain text (possibly multi-line) ingredient copy.
    ingredients: Mapped[str | None] = mapped_column(Text)
    care_instructions: Mapped[str | None] = mapped_column(Text)
    # JSON sections, each optional; storefront hides a section when absent:
    #   highlights:  ["30 Gummies", "15 Servings", ...] or [{icon,label}]
    #   benefits:    [{icon, title, text}]        → "Why You'll Love It"
    #   specifications: {key: value}
    #   box_contents:   [str]
    #   usage_steps: [{label, text}]              → "What to expect" timeline
    #   faqs:        [{q, a}]
    highlights: Mapped[list | None] = mapped_column(JSON, nullable=True)
    benefits: Mapped[list | None] = mapped_column(JSON, nullable=True)
    specifications: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    box_contents: Mapped[list | None] = mapped_column(JSON, nullable=True)
    usage_steps: Mapped[list | None] = mapped_column(JSON, nullable=True)
    faqs: Mapped[list | None] = mapped_column(JSON, nullable=True)

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    category: Mapped[Category | None] = relationship(back_populates="products")

    images: Mapped[list["ProductImage"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductImage.position",
    )

    # Per-product tax. Multiple taxes are summed (e.g. GST + cess).
    taxes: Mapped[list["Tax"]] = relationship(
        secondary="product_taxes", back_populates="products", lazy="selectin"
    )

    # Denormalized rating aggregates — recomputed by ReviewService on every
    # review mutation. Listed alongside the product so listing/card queries
    # don't need to AVG() across reviews on the read path.
    rating_avg: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), nullable=False, default=Decimal("0.00")
    )
    rating_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Map of "1".."5" -> count. Stored as JSON for cheap one-shot fetch of the
    # 5-bar histogram. Null is treated as "no reviews yet" in the schema layer.
    rating_distribution: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    reviews: Mapped[list["Review"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class ProductImage(Base, IDMixin, TimestampMixin):
    __tablename__ = "product_images"

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
    )
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    position: Mapped[int] = mapped_column(default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    product: Mapped[Product] = relationship(back_populates="images")
