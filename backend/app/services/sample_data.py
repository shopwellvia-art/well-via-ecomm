"""Per-domain sample-data seeders for the admin Danger zone.

Each ``seed_*`` function loads a small, realistic slice of dummy data for one
domain group and returns a ``{table: rows_created}`` summary. They are the
"add some dummy data" companion to the scoped truncate buttons: after wiping a
domain you can press *Seed sample data* to repopulate just that domain — or
leave it empty by not pressing it.

Design rules every seeder follows:
  * **Idempotent** — re-running skips rows that already exist (matched on a
    natural key: slug / sku / code / name / key), so a double-click can't 500
    on a unique-constraint violation.
  * **Self-healing prerequisites** — a seeder that needs upstream rows (orders
    need a buyer + products; reviews need products) creates or reuses them
    rather than failing. It never touches the bootstrap admin or the RBAC set.
  * **No commit** — the caller owns the transaction so the whole seed is atomic
    and can be audit-logged in the same unit of work.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.coupon import Coupon, DiscountType
from app.models.customer import Customer
from app.models.hero_slide import HeroSlide
from app.models.loyalty import EarnRule, RedemptionTier, VipTier
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Category, Product, ProductImage
from app.models.review import Review
from app.models.user import User

# A storefront account used as the buyer for sample orders when no other
# non-admin user exists. Mirrors scripts/seed.py CUSTOMER.
_SAMPLE_CUSTOMER = {
    "email": "customer@lumen.store",
    "first_name": "Wellvia",
    "last_name": "Customer",
    "password": "Customer123!",
}

_CATEGORIES = [
    ("Audio", "audio"),
    ("Wearables", "wearables"),
    ("Workspace", "workspace"),
    ("Lighting", "lighting"),
]

# (sku, name, category_slug, price, stock, description)
_PRODUCTS = [
    ("AUD-AURA-01", "Aura Wireless Headphones", "audio", "299.00", 24,
     "Adaptive noise cancellation and 40-hour battery in a feather-light frame."),
    ("AUD-PULSE-02", "Pulse Earbuds Pro", "audio", "179.00", 40,
     "Studio-grade sound with spatial audio and a pocket-sized charging case."),
    ("WER-ORBIT-01", "Orbit Smartwatch", "wearables", "349.00", 18,
     "A titanium smartwatch with always-on display and multi-day battery."),
    ("WER-TRACE-02", "Trace Fitness Band", "wearables", "89.00", 60,
     "Lightweight tracking for sleep, heart rate, and daily movement."),
    ("WRK-FIELD-01", "Field Mechanical Keyboard", "workspace", "169.00", 30,
     "Hot-swappable switches and a machined aluminium deck built to last."),
    ("WRK-GLIDE-02", "Glide Wireless Mouse", "workspace", "79.00", 15,
     "Silent, precise, and contoured for all-day comfort."),
    ("LGT-GLOW-01", "Glow Desk Lamp", "lighting", "139.00", 16,
     "Tunable warm-to-cool light with a whisper-quiet dimmer."),
    ("LGT-NOVA-02", "Nova Ambient Light Bar", "lighting", "99.00", 8,
     "Reactive ambient lighting that follows the mood of your room."),
]


def _bump(summary: dict[str, int], table: str, n: int = 1) -> None:
    summary[table] = summary.get(table, 0) + n


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #
def seed_catalog(db: Session) -> dict[str, int]:
    """Categories + products (with a primary image each). Idempotent on slug/sku."""
    summary: dict[str, int] = {}

    cat_by_slug: dict[str, Category] = {}
    for name, slug in _CATEGORIES:
        cat = db.query(Category).filter(Category.slug == slug).one_or_none()
        if cat is None:
            cat = Category(name=name, slug=slug)
            db.add(cat)
            db.flush()
            _bump(summary, "categories")
        cat_by_slug[slug] = cat

    for sku, name, slug, price, stock, description in _PRODUCTS:
        if db.query(Product).filter(Product.sku == sku).one_or_none() is not None:
            continue
        image_url = f"https://picsum.photos/seed/{sku}/800/800"
        product = Product(
            sku=sku,
            name=name,
            description=description,
            price=Decimal(price),
            stock=stock,
            category_id=cat_by_slug[slug].id,
            image_url=image_url,
        )
        db.add(product)
        db.flush()
        db.add(ProductImage(product_id=product.id, url=image_url, position=0, is_primary=True))
        _bump(summary, "products")
        _bump(summary, "product_images")

    return summary


# --------------------------------------------------------------------------- #
# Orders & payments
# --------------------------------------------------------------------------- #
def _ensure_buyer(db: Session) -> User:
    """Return a non-admin user to own sample orders, creating the sample
    storefront account if the only users are admins. Never returns an admin if
    a customer exists."""
    buyer = (
        db.query(User)
        .filter(User.is_admin.is_(False))
        .order_by(User.id)
        .first()
    )
    if buyer is not None:
        return buyer

    buyer = User(
        email=_SAMPLE_CUSTOMER["email"],
        hashed_password=hash_password(_SAMPLE_CUSTOMER["password"]),
        is_active=True,
        is_admin=False,
    )
    db.add(buyer)
    db.flush()
    db.add(
        Customer(
            user_id=buyer.id,
            first_name=_SAMPLE_CUSTOMER["first_name"],
            last_name=_SAMPLE_CUSTOMER["last_name"],
        )
    )
    return buyer


def seed_orders(db: Session) -> dict[str, int]:
    """A handful of sample orders across the status lifecycle. Ensures a buyer
    and a catalog exist first (creating them if the domain was just wiped).

    Idempotent: skips entirely if any orders already exist, so re-pressing Seed
    doesn't stack duplicate sample orders (orders have no natural key to dedup on).
    """
    summary: dict[str, int] = {}

    if db.query(Order).count() > 0:
        return summary  # already populated — don't pile on more sample orders

    if db.query(Product).count() == 0:
        summary.update(seed_catalog(db))
    products = db.query(Product).order_by(Product.id).limit(5).all()
    if not products:
        return summary  # nothing to sell — leave empty rather than fabricate

    buyer = _ensure_buyer(db)

    # (status, [(product_index, qty), ...]) — kept small and deterministic.
    blueprints = [
        (OrderStatus.DELIVERED, [(0, 1), (1, 2)]),
        (OrderStatus.SHIPPED, [(2, 1)]),
        (OrderStatus.PAID, [(3, 1), (0, 1)]),
        (OrderStatus.PENDING, [(1, 1)]),
    ]
    snapshot = {
        "full_name": "Wellvia Customer",
        "line1": "12 Residency Road",
        "city": "Bengaluru",
        "state": "Karnataka",
        "pincode": "560025",
        "country": "IN",
    }

    for status, lines in blueprints:
        subtotal = Decimal("0.00")
        items: list[OrderItem] = []
        for idx, qty in lines:
            product = products[idx % len(products)]
            unit_price = Decimal(product.price)
            subtotal += unit_price * qty
            items.append(
                OrderItem(
                    product_id=product.id,
                    quantity=qty,
                    unit_price=unit_price,
                    unit_cost=product.cost,
                )
            )
        order = Order(
            user_id=buyer.id,
            status=status,
            subtotal=subtotal,
            total_amount=subtotal,
            currency="INR",
            payment_method="prepaid",
            shipping_address=f"{snapshot['line1']}, {snapshot['city']}",
            shipping_pincode=snapshot["pincode"],
            shipping_address_snapshot=snapshot,
            items=items,
        )
        db.add(order)
        _bump(summary, "orders")
        _bump(summary, "order_items", len(items))

    db.flush()
    return summary


# --------------------------------------------------------------------------- #
# Reviews
# --------------------------------------------------------------------------- #
_REVIEW_BLUEPRINTS = [
    (5, "Stellar build quality", "Worth every rupee — sounds incredible.", "Aarav S."),
    (4, "Really good, minor niggles", "Battery is great; app could be smoother.", "Priya M."),
    (5, "Daily driver", "Haven't put it down since it arrived.", "Rohan K."),
]


def _recompute_product_rating(db: Session, product: Product) -> None:
    """Keep the denormalized rating aggregates on the product in sync with the
    approved reviews we just inserted (the read path trusts these columns)."""
    ratings = [
        r.rating
        for r in db.query(Review)
        .filter(Review.product_id == product.id, Review.is_approved.is_(True))
        .all()
    ]
    count = len(ratings)
    product.rating_count = count
    if count:
        product.rating_avg = Decimal(str(round(sum(ratings) / count, 2)))
        dist = {str(n): 0 for n in range(1, 6)}
        for r in ratings:
            dist[str(r)] += 1
        product.rating_distribution = dist
    else:
        product.rating_avg = Decimal("0.00")
        product.rating_distribution = None


def seed_reviews(db: Session) -> dict[str, int]:
    """A few approved, admin-authored (user_id=NULL) reviews on the first
    products. Recomputes each product's rating aggregates afterwards."""
    summary: dict[str, int] = {}

    if db.query(Product).count() == 0:
        summary.update(seed_catalog(db))
    products = db.query(Product).order_by(Product.id).limit(4).all()

    for product in products:
        touched = False
        for rating, title, body, author in _REVIEW_BLUEPRINTS:
            # author_name is the natural key here — user_id is NULL so the
            # (product_id, user_id) unique constraint doesn't apply.
            exists = (
                db.query(Review)
                .filter(
                    Review.product_id == product.id,
                    Review.author_name == author,
                )
                .one_or_none()
            )
            if exists is not None:
                continue
            db.add(
                Review(
                    product_id=product.id,
                    user_id=None,
                    author_name=author,
                    rating=rating,
                    title=title,
                    body=body,
                    is_verified_purchase=True,
                    is_approved=True,
                )
            )
            _bump(summary, "reviews")
            touched = True
        if touched:
            db.flush()
            _recompute_product_rating(db, product)

    return summary


# --------------------------------------------------------------------------- #
# Loyalty & marketing
# --------------------------------------------------------------------------- #
def seed_loyalty(db: Session) -> dict[str, int]:
    """VIP ladder, earn rules, redemption tiers and a couple of promo coupons."""
    summary: dict[str, int] = {}

    vip_tiers = [
        ("Bronze", 0, "1.00", "Welcome perks", "#CD7F32", 0),
        ("Silver", 1000, "1.25", "25% bonus points + early sales", "#C0C0C0", 1),
        ("Gold", 5000, "1.50", "50% bonus points + free shipping", "#FFD700", 2),
    ]
    for name, threshold, mult, benefits, color, order in vip_tiers:
        if db.query(VipTier).filter(VipTier.name == name).one_or_none() is None:
            db.add(
                VipTier(
                    name=name,
                    threshold_lifetime_points=threshold,
                    earn_multiplier=Decimal(mult),
                    benefits=benefits,
                    color=color,
                    sort_order=order,
                )
            )
            _bump(summary, "vip_tiers")

    earn_rules = [
        ("signup_bonus", "Sign-up bonus", "Points granted on registration", 100),
        ("place_order", "Order reward", "Points per ₹ of order subtotal", 1),
        ("write_review", "Review reward", "Points for a published review", 50),
    ]
    for key, display, desc, value in earn_rules:
        if db.query(EarnRule).filter(EarnRule.key == key).one_or_none() is None:
            db.add(
                EarnRule(key=key, display_name=display, description=desc, points_value=value)
            )
            _bump(summary, "earn_rules")

    redemption_tiers = [
        ("₹100 off", 1000, "fixed", "100.00", None),
        ("10% off (max ₹500)", 2500, "percent", "10.00", "500.00"),
    ]
    for name, cost, dtype, value, cap in redemption_tiers:
        if db.query(RedemptionTier).filter(RedemptionTier.name == name).one_or_none() is None:
            db.add(
                RedemptionTier(
                    name=name,
                    cost_points=cost,
                    discount_type=dtype,
                    discount_value=Decimal(value),
                    max_discount=Decimal(cap) if cap else None,
                )
            )
            _bump(summary, "redemption_tiers")

    coupons = [
        ("WELCOME10", "10% off your first order", DiscountType.PERCENT, "10.00", "500.00", "1000.00"),
        ("FLAT200", "₹200 off orders over ₹1,999", DiscountType.FIXED, "200.00", "1999.00", None),
    ]
    for code, desc, dtype, value, min_amt, max_disc in coupons:
        if db.query(Coupon).filter(Coupon.code == code).one_or_none() is None:
            db.add(
                Coupon(
                    code=code,
                    description=desc,
                    discount_type=dtype,
                    discount_value=Decimal(value),
                    min_order_amount=Decimal(min_amt) if min_amt else None,
                    max_discount=Decimal(max_disc) if max_disc else None,
                    is_active=True,
                )
            )
            _bump(summary, "coupons")

    db.flush()
    return summary


# --------------------------------------------------------------------------- #
# Storefront content
# --------------------------------------------------------------------------- #
_HERO_SLIDES = [
    {
        "image_url": "https://picsum.photos/seed/hero-audio/1600/600",
        "alt": "Audio sale banner",
        "eyebrow": "Limited time",
        "heading": "Sound, perfected",
        "subtext": "Up to 30% off premium audio.",
        "cta_label": "Shop audio",
        "cta_href": "/category/audio",
        "sort_order": 0,
    },
    {
        "image_url": "https://picsum.photos/seed/hero-wearables/1600/600",
        "alt": "Wearables banner",
        "eyebrow": "New arrivals",
        "heading": "Wear the future",
        "subtext": "Smartwatches & bands that keep up.",
        "cta_label": "Explore wearables",
        "cta_href": "/category/wearables",
        "sort_order": 1,
    },
]


def seed_content(db: Session) -> dict[str, int]:
    """Sample hero-carousel slides. (site_pages / footer_config are single-row
    JSON documents that fall back to built-in defaults when empty, so there is
    nothing meaningful to seed there.)"""
    summary: dict[str, int] = {}
    for slide in _HERO_SLIDES:
        exists = (
            db.query(HeroSlide).filter(HeroSlide.image_url == slide["image_url"]).one_or_none()
        )
        if exists is not None:
            continue
        db.add(HeroSlide(is_active=True, kind="photo", text_theme="light", **slide))
        _bump(summary, "hero_slides")
    db.flush()
    return summary
