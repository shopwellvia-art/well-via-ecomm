"""Seed an admin user and a sample catalog.

Idempotent — re-running skips records that already exist.
Run inside the backend container:

    docker run --rm --env-file ./backend/.env -v <repo>/backend:/app -w /app \
        ecom-backend python scripts/seed.py
"""
import os
import sys
from decimal import Decimal

# Make the `app` package importable when run as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.customer import Customer  # noqa: E402
from app.models.product import Category, Product  # noqa: E402
from app.models.user import User  # noqa: E402

ADMIN = {
    "email": "vinay@gmail.com",
    "full_name": "Vinay",
    "password": "vinay@123",
}

# Fixed storefront account for local/dev testing (quick-login button on /login).
CUSTOMER = {
    "email": "customer@lumen.store",
    "full_name": "Lumen Customer",
    "password": "Customer123!",
}

CATEGORIES = [
    ("Audio", "audio"),
    ("Wearables", "wearables"),
    ("Workspace", "workspace"),
    ("Lighting", "lighting"),
]

# (sku, name, category_slug, price, stock, description)
PRODUCTS = [
    ("AUD-AURA-01", "Aura Wireless Headphones", "audio", "299.00", 24,
     "Adaptive noise cancellation and 40-hour battery in a feather-light frame."),
    ("AUD-PULSE-02", "Pulse Earbuds Pro", "audio", "179.00", 40,
     "Studio-grade sound with spatial audio and a pocket-sized charging case."),
    ("AUD-RESON-03", "Resonance Bookshelf Speaker", "audio", "449.00", 12,
     "A warm, room-filling sound stage crafted from solid walnut."),
    ("WER-ORBIT-01", "Orbit Smartwatch", "wearables", "349.00", 18,
     "A titanium smartwatch with always-on display and multi-day battery."),
    ("WER-TRACE-02", "Trace Fitness Band", "wearables", "89.00", 60,
     "Lightweight tracking for sleep, heart rate, and daily movement."),
    ("WER-HALO-03", "Halo Smart Ring", "wearables", "259.00", 5,
     "Discreet wellness insights in a polished, water-resistant ring."),
    ("WRK-FIELD-01", "Field Mechanical Keyboard", "workspace", "169.00", 30,
     "Hot-swappable switches and a machined aluminium deck built to last."),
    ("WRK-GLIDE-02", "Glide Wireless Mouse", "workspace", "79.00", 0,
     "Silent, precise, and contoured for all-day comfort."),
    ("WRK-MONO-03", "Monolith Laptop Stand", "workspace", "119.00", 22,
     "An anodized stand that lifts your screen to a natural eye line."),
    ("LGT-LUMEN-01", "Lumen Desk Lamp", "lighting", "139.00", 16,
     "Tunable warm-to-cool light with a whisper-quiet dimmer."),
    ("LGT-NOVA-02", "Nova Ambient Light Bar", "lighting", "99.00", 3,
     "Reactive ambient lighting that follows the mood of your room."),
    ("LGT-ECLIP-03", "Eclipse Floor Lamp", "lighting", "229.00", 9,
     "A sculptural floor lamp that doubles as a quiet statement piece."),
]


def seed() -> None:
    db = SessionLocal()
    created = {"admin": 0, "customer": 0, "categories": 0, "products": 0}
    try:
        # Admin user
        admin = db.query(User).filter(User.email == ADMIN["email"]).one_or_none()
        if admin is None:
            admin = User(
                email=ADMIN["email"],
                hashed_password=hash_password(ADMIN["password"]),
                is_active=True,
                is_admin=True,
            )
            db.add(admin)
            db.flush()  # need admin.id for the customer row
            # Profile now lives on the customer satellite. account_status
            # defaults to ACTIVE at the model level.
            first, _, last = ADMIN["full_name"].partition(" ")
            db.add(
                Customer(
                    user_id=admin.id,
                    first_name=first or None,
                    last_name=last or None,
                )
            )
            created["admin"] = 1
        elif not admin.is_admin:
            admin.is_admin = True

        # Test customer (plain storefront account, no admin rights)
        customer = (
            db.query(User).filter(User.email == CUSTOMER["email"]).one_or_none()
        )
        if customer is None:
            customer = User(
                email=CUSTOMER["email"],
                hashed_password=hash_password(CUSTOMER["password"]),
                is_active=True,
                is_admin=False,
            )
            db.add(customer)
            db.flush()
            first, _, last = CUSTOMER["full_name"].partition(" ")
            db.add(
                Customer(
                    user_id=customer.id,
                    first_name=first or None,
                    last_name=last or None,
                )
            )
            created["customer"] = 1

        # Categories
        cat_by_slug = {}
        for name, slug in CATEGORIES:
            cat = db.query(Category).filter(Category.slug == slug).one_or_none()
            if cat is None:
                cat = Category(name=name, slug=slug)
                db.add(cat)
                db.flush()
                created["categories"] += 1
            cat_by_slug[slug] = cat

        # Products
        for sku, name, slug, price, stock, description in PRODUCTS:
            exists = db.query(Product).filter(Product.sku == sku).one_or_none()
            if exists is not None:
                continue
            db.add(
                Product(
                    sku=sku,
                    name=name,
                    description=description,
                    price=Decimal(price),
                    stock=stock,
                    category_id=cat_by_slug[slug].id,
                )
            )
            created["products"] += 1

        db.commit()
    finally:
        db.close()

    print(
        f"Seed complete — admin: +{created['admin']}, customer: +{created['customer']}, "
        f"categories: +{created['categories']}, products: +{created['products']}"
    )
    print(f"Admin login: {ADMIN['email']} / {ADMIN['password']}")
    print(f"Customer login: {CUSTOMER['email']} / {CUSTOMER['password']}")


if __name__ == "__main__":
    seed()
