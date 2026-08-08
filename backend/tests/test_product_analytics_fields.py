"""Capture layer for the four product fields the analytics pipeline already
consumes but has never been fed: `reorder_point`, `hsn_code`, `brand` and
`shelf_life_days`.

Why each of them exists is documented on the model and in migration
`f4c28d19ab60`; what this suite protects is the part that is easy to break
later and expensive to notice:

  1. **NULL is not zero.** `agg_inventory_daily.reorder_gap` is
     `stock_close - reorder_point` and stores NULL to mean "no reorder point
     configured", which is a different statement from 0 ("reorder at empty").
     If anybody ever gives `reorder_point` a default, every SKU in the catalog
     silently starts reporting as sitting exactly at its reorder point and the
     low-stock view becomes noise. Tests here assert NULL and 0 both round-trip
     and stay distinguishable end to end.
  2. **HSN is either legal or absent.** 4, 6 and 8 digits are the only legal
     granularities on an Indian GST invoice. A 5-digit value must be rejected,
     not padded — guessing the missing digit would put an invented tariff
     heading on a filing. Non-ASCII digits ("٤", "²") pass `str.isdigit()` and
     are covered here for that reason.
  3. **"No brand" is one value.** An empty string must land as NULL so brand
     rollups never have to know about two spellings of nothing.
  4. **Nothing became implicitly required.** Every product that predates these
     columns has NULL in all four and must still serialise and update.
  5. **The two schema artifacts still agree.** The Alembic revision (CI, local)
     and the hand-applied SQL twin (shared remote MySQL, which is on a lineage
     this repo does not contain) can drift silently; if they do, production 500s
     while CI stays green. Same failure mode `test_analytics_schema.py` guards
     for the analytics tables.

House style: no shared DB fixture — every test owns its `SessionLocal()` and
tears down in `finally`. Sandbox is the `TESTPAF-` SKU prefix, swept before and
after the module. Nothing here touches `PERF-` rows or filters by date.

    docker exec wvana-py python -m pytest tests/test_product_analytics_fields.py -q
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, inspect, text

from app.db.session import SessionLocal
from app.models.product import Product
from app.schemas.product import (
    ProductAdminRead,
    ProductCreate,
    ProductRead,
    ProductUpdate,
)
from app.services.product_service import ProductService
from tests.conftest import get_test_admin_token

# The four columns under test, with the shape they must have in the database.
# (type family, length or None, nullable, must have no server default)
ANALYTICS_COLUMNS: dict[str, tuple[str, int | None]] = {
    "reorder_point": ("INT", None),
    "hsn_code": ("STRING", 8),
    "brand": ("STRING", 120),
    "shelf_life_days": ("INT", None),
}

SKU_PREFIX = "TESTPAF-"

_BACKEND = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    _BACKEND / "alembic" / "versions" / "f4c28d19ab60_add_product_analytics_fields.py"
)
SQL_TWIN_PATH = _BACKEND / "scripts" / "sql" / "2026-07-31_product_analytics_fields.sql"


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

def _purge_sandbox() -> None:
    """Delete every `TESTPAF-` product. Scoped by prefix, never by date, so a
    run can never reach a real row or a `PERF-` benchmark fixture."""
    db = SessionLocal()
    try:
        db.execute(delete(Product).where(Product.sku.like(f"{SKU_PREFIX}%")))
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="module", autouse=True)
def _sandbox():
    """Belt and braces around the per-test `finally` blocks: a test that dies
    mid-assert still leaves the catalog as it found it."""
    _purge_sandbox()
    yield
    _purge_sandbox()


def _new_sku() -> str:
    return f"{SKU_PREFIX}{uuid.uuid4().hex[:10]}"


def _base_payload(sku: str, **overrides) -> dict:
    payload = {
        "sku": sku,
        "name": f"Analytics probe {sku}",
        "price": Decimal("199.00"),
        "stock": 12,
    }
    payload.update(overrides)
    return payload


def _create(db, sku: str, **overrides) -> Product:
    return ProductService(db).create(ProductCreate(**_base_payload(sku, **overrides)))


# ---------------------------------------------------------------------------
# 1. Round-trip: create and update with all four, read back exactly
# ---------------------------------------------------------------------------

def test_create_persists_all_four_verbatim():
    sku = _new_sku()
    db = SessionLocal()
    try:
        product = _create(
            db,
            sku,
            reorder_point=25,
            hsn_code="21069099",
            brand="Wellvia",
            shelf_life_days=540,
        )
        assert product.reorder_point == 25
        assert product.hsn_code == "21069099"
        assert product.brand == "Wellvia"
        assert product.shelf_life_days == 540

        # Read back through the response schema — the API contract, not just
        # the ORM object still in the identity map.
        read = ProductAdminRead.model_validate(product)
        assert read.reorder_point == 25
        assert read.hsn_code == "21069099"
        assert read.brand == "Wellvia"
        assert read.shelf_life_days == 540
    finally:
        db.close()
        _purge_sandbox()


def test_update_sets_and_clears_all_four():
    sku = _new_sku()
    db = SessionLocal()
    try:
        product = _create(db, sku)
        assert product.reorder_point is None, "must start unconfigured"

        service = ProductService(db)
        updated = service.update(
            product.id,
            ProductUpdate(
                reorder_point=8,
                hsn_code="0904",
                brand="Himalaya",
                shelf_life_days=365,
            ),
        )
        assert (
            updated.reorder_point,
            updated.hsn_code,
            updated.brand,
            updated.shelf_life_days,
        ) == (8, "0904", "Himalaya", 365)

        # An explicit null clears; that is how a mis-entered HSN code or a
        # retired reorder point is removed rather than zeroed.
        cleared = service.update(
            product.id,
            ProductUpdate(
                reorder_point=None,
                hsn_code=None,
                brand=None,
                shelf_life_days=None,
            ),
        )
        assert cleared.reorder_point is None
        assert cleared.hsn_code is None
        assert cleared.brand is None
        assert cleared.shelf_life_days is None
    finally:
        db.close()
        _purge_sandbox()


def test_update_leaves_omitted_analytics_fields_alone():
    """`exclude_unset` semantics: editing the price must not wipe the HSN code.

    Without this, every save from a form that does not know about these fields
    would quietly blank a product's compliance data.
    """
    sku = _new_sku()
    db = SessionLocal()
    try:
        product = _create(db, sku, hsn_code="090411", reorder_point=0, brand="Dabur")
        updated = ProductService(db).update(
            product.id, ProductUpdate(price=Decimal("249.00"))
        )
        assert updated.hsn_code == "090411"
        assert updated.reorder_point == 0
        assert updated.brand == "Dabur"
    finally:
        db.close()
        _purge_sandbox()


# ---------------------------------------------------------------------------
# 2. NULL vs 0 for reorder_point are different facts
# ---------------------------------------------------------------------------

def test_reorder_point_null_and_zero_are_distinct_and_both_round_trip():
    """The distinction `agg_inventory_daily.reorder_gap` is built on.

    NULL = "no reorder point configured" (reorder_gap must stay NULL).
    0     = "reorder only once the shelf is empty" (reorder_gap = stock_close).
    Asserted at the SQL level too, because an ORM default or a schema default
    would be invisible from Python but very visible in the rollup.
    """
    unset_sku, zero_sku = _new_sku(), _new_sku()
    db = SessionLocal()
    try:
        unset = _create(db, unset_sku)
        zero = _create(db, zero_sku, reorder_point=0)

        assert unset.reorder_point is None
        assert zero.reorder_point == 0
        assert zero.reorder_point is not None, "0 must not be falsy-collapsed to None"

        rows = dict(
            db.execute(
                text(
                    "SELECT sku, reorder_point FROM products "
                    "WHERE sku IN (:a, :b)"
                ),
                {"a": unset_sku, "b": zero_sku},
            ).all()
        )
        assert rows[unset_sku] is None, "no reorder point must be stored as NULL"
        assert rows[zero_sku] == 0, "an explicit 0 must be stored as 0"

        # And the distinction survives serialisation, which is where a
        # `or 0` / `if not value` idiom would destroy it.
        assert ProductAdminRead.model_validate(unset).reorder_point is None
        assert ProductAdminRead.model_validate(zero).reorder_point == 0
    finally:
        db.close()
        _purge_sandbox()


def test_reorder_point_rejects_negative():
    with pytest.raises(ValidationError):
        ProductCreate(**_base_payload(_new_sku(), reorder_point=-1))
    with pytest.raises(ValidationError):
        ProductUpdate(reorder_point=-1)


def test_reorder_point_has_no_database_default():
    """A DEFAULT 0 here would silently convert "unknown" into "at threshold"
    for every product ever inserted outside the API."""
    db = SessionLocal()
    try:
        col = next(
            c
            for c in inspect(db.get_bind()).get_columns("products")
            if c["name"] == "reorder_point"
        )
        assert col["default"] is None, f"reorder_point has a server default: {col}"
        assert col["nullable"] is True
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. HSN validation
# ---------------------------------------------------------------------------

VALID_HSN = ["0904", "090411", "09041110", "2106", "21069099", "0000"]
INVALID_HSN = [
    "09041",        # 5 digits — not a legal granularity, and must not be padded
    "0904111",      # 7 digits — likewise
    "090411101",    # 9 digits — longer than the column and than any real code
    "12A4",         # letters
    "09-04",        # punctuation
    "",             # empty
    "   ",          # empty after trim
    "0904 1110",    # internal space
    "٠٩٠٤",         # Arabic-Indic digits: str.isdigit() says True, GST says no
    "²²²²",         # superscripts: same trap
    "+904",
]


@pytest.mark.parametrize("code", VALID_HSN)
def test_hsn_accepts_legal_lengths(code: str):
    assert ProductCreate(**_base_payload(_new_sku(), hsn_code=code)).hsn_code == code
    assert ProductUpdate(hsn_code=code).hsn_code == code


@pytest.mark.parametrize("code", INVALID_HSN)
def test_hsn_rejects_everything_else(code: str):
    with pytest.raises(ValidationError) as exc:
        ProductCreate(**_base_payload(_new_sku(), hsn_code=code))
    assert "4, 6 or 8 digits" in str(exc.value), "the error must say what is legal"

    with pytest.raises(ValidationError):
        ProductUpdate(hsn_code=code)


def test_hsn_is_trimmed_not_coerced():
    """Surrounding whitespace is a paste artifact and is stripped. Nothing else
    is fixed up: a short code is an error, never zero-padded."""
    assert ProductCreate(**_base_payload(_new_sku(), hsn_code="  0904 ")).hsn_code == "0904"
    with pytest.raises(ValidationError):
        ProductCreate(**_base_payload(_new_sku(), hsn_code="904"))


def test_hsn_none_is_allowed_and_means_unclassified():
    product = ProductCreate(**_base_payload(_new_sku(), hsn_code=None))
    assert product.hsn_code is None


def test_hsn_persists_leading_zeros():
    """The reason this column is a VARCHAR and not an INT."""
    sku = _new_sku()
    db = SessionLocal()
    try:
        product = _create(db, sku, hsn_code="0904")
        stored = db.execute(
            text("SELECT hsn_code FROM products WHERE sku = :sku"), {"sku": sku}
        ).scalar_one()
        assert stored == "0904"
        assert ProductRead.model_validate(product).hsn_code == "0904"
    finally:
        db.close()
        _purge_sandbox()


# ---------------------------------------------------------------------------
# 4. brand: "" is NULL, and it is trimmed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_brand_empty_string_becomes_null(blank: str):
    sku = _new_sku()
    db = SessionLocal()
    try:
        product = _create(db, sku, brand=blank)
        assert product.brand is None

        stored = db.execute(
            text("SELECT brand FROM products WHERE sku = :sku"), {"sku": sku}
        ).scalar_one()
        assert stored is None, f'brand persisted as {stored!r}, not NULL'

        # The count is the assertion that matters: exactly one representation
        # of "no brand" may exist in the column.
        empties = db.execute(
            text(
                "SELECT COUNT(*) FROM products "
                "WHERE sku LIKE :p AND brand IS NOT NULL AND TRIM(brand) = ''"
            ),
            {"p": f"{SKU_PREFIX}%"},
        ).scalar_one()
        assert empties == 0
    finally:
        db.close()
        _purge_sandbox()


def test_brand_update_with_empty_string_clears_to_null():
    sku = _new_sku()
    db = SessionLocal()
    try:
        product = _create(db, sku, brand="Wellvia")
        updated = ProductService(db).update(product.id, ProductUpdate(brand="  "))
        assert updated.brand is None
    finally:
        db.close()
        _purge_sandbox()


def test_brand_is_trimmed_and_length_capped():
    assert ProductCreate(**_base_payload(_new_sku(), brand="  Wellvia  ")).brand == "Wellvia"
    # 120 is the width of `analytics_order_line.brand_snapshot`; an over-long
    # brand must error rather than be truncated into a different brand.
    assert len(ProductCreate(**_base_payload(_new_sku(), brand="b" * 120)).brand) == 120
    with pytest.raises(ValidationError):
        ProductCreate(**_base_payload(_new_sku(), brand="b" * 121))
    with pytest.raises(ValidationError):
        ProductUpdate(brand="b" * 121)


def test_brand_width_matches_the_reserved_snapshot_column():
    """`analytics_order_line.brand_snapshot` was reserved for this field. If the
    widths ever diverge, the snapshot truncates and the fact table disagrees
    with the catalog about what a brand is called."""
    from app.models.analytics_facts import AnalyticsOrderLine

    assert (
        Product.__table__.c.brand.type.length
        == AnalyticsOrderLine.__table__.c.brand_snapshot.type.length
        == 120
    )


# ---------------------------------------------------------------------------
# 5. shelf_life_days rejects 0 and negatives
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("days", [0, -1, -365])
def test_shelf_life_rejects_zero_and_negatives(days: int):
    """0 is rejected on purpose. A zero-day shelf life is not a product, and
    permitting it would let "expires today" collide with the NULL that means
    "not tracked" the moment anything rounded one into the other."""
    with pytest.raises(ValidationError):
        ProductCreate(**_base_payload(_new_sku(), shelf_life_days=days))
    with pytest.raises(ValidationError):
        ProductUpdate(shelf_life_days=days)


@pytest.mark.parametrize("days", [1, 90, 730])
def test_shelf_life_accepts_positive(days: int):
    assert ProductCreate(**_base_payload(_new_sku(), shelf_life_days=days)).shelf_life_days == days


def test_shelf_life_null_means_not_tracked():
    sku = _new_sku()
    db = SessionLocal()
    try:
        product = _create(db, sku)
        assert product.shelf_life_days is None
        assert ProductAdminRead.model_validate(product).shelf_life_days is None
    finally:
        db.close()
        _purge_sandbox()


# ---------------------------------------------------------------------------
# 6. Pre-existing products (NULL everywhere) are unaffected
# ---------------------------------------------------------------------------

def test_legacy_product_with_all_nulls_serialises_and_updates():
    """Nothing became implicitly required.

    Simulates a row that predates the migration by INSERTing it with all four
    columns NULL, then does what the app does to it: serialise it, and PATCH an
    unrelated field.
    """
    sku = _new_sku()
    db = SessionLocal()
    try:
        legacy = Product(sku=sku, name=f"Legacy {sku}", price=Decimal("99.00"), stock=3)
        db.add(legacy)
        db.commit()
        db.refresh(legacy)

        assert legacy.reorder_point is None
        assert legacy.hsn_code is None
        assert legacy.brand is None
        assert legacy.shelf_life_days is None

        read = ProductAdminRead.model_validate(legacy)
        dumped = read.model_dump()
        for field in ANALYTICS_COLUMNS:
            assert field in dumped, f"{field} missing from the read schema"
            assert dumped[field] is None

        updated = ProductService(db).update(legacy.id, ProductUpdate(stock=7))
        assert updated.stock == 7
        assert updated.reorder_point is None
        assert updated.hsn_code is None
        assert updated.brand is None
        assert updated.shelf_life_days is None
    finally:
        db.close()
        _purge_sandbox()


def test_create_without_any_analytics_field_is_valid():
    """The four fields must never become required on the create path."""
    product = ProductCreate(**_base_payload(_new_sku()))
    assert product.reorder_point is None
    assert product.hsn_code is None
    assert product.brand is None
    assert product.shelf_life_days is None


# ---------------------------------------------------------------------------
# 7. Schema reflection — ORM matches DB, migration matches its SQL twin
# ---------------------------------------------------------------------------

def _type_family(rendered: str) -> str:
    t = rendered.upper()
    for key in ("VARCHAR", "CHAR", "TEXT"):
        if key in t:
            return "STRING"
    for key in ("BIGINT", "SMALLINT", "INTEGER", "INT"):
        if key in t:
            return "INT"
    return t


@pytest.mark.parametrize("name", sorted(ANALYTICS_COLUMNS))
def test_column_matches_orm_and_database(name: str):
    expected_family, expected_length = ANALYTICS_COLUMNS[name]

    orm_col = Product.__table__.c[name]
    assert orm_col.nullable is True, f"{name} must stay nullable in the ORM"
    assert orm_col.server_default is None, f"{name} must have no server default"
    assert orm_col.default is None, f"{name} must have no client-side default"
    assert _type_family(str(orm_col.type)) == expected_family
    assert getattr(orm_col.type, "length", None) == expected_length

    db = SessionLocal()
    try:
        db_cols = {c["name"]: c for c in inspect(db.get_bind()).get_columns("products")}
    finally:
        db.close()

    assert name in db_cols, (
        f"{name} is declared in the ORM but missing from the database. Run "
        "`alembic upgrade head` locally, or apply "
        "backend/scripts/sql/2026-07-31_product_analytics_fields.sql on the "
        "shared remote DB."
    )
    actual = db_cols[name]
    assert _type_family(str(actual["type"])) == expected_family, actual
    assert actual["nullable"] is True, actual
    assert actual["default"] is None, actual
    if expected_length is not None:
        assert f"({expected_length})" in str(actual["type"]).upper(), actual


_MIGRATION_COLUMN_RE = re.compile(
    r"""sa\.Column\(\s*["'](?P<name>\w+)["']\s*,\s*"""
    r"""sa\.(?P<type>\w+)\((?P<args>[^)]*)\)\s*,\s*nullable=(?P<nullable>True|False)""",
    re.VERBOSE,
)
_SQL_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+products\s+ADD\s+COLUMN\s+(?P<name>\w+)\s+"
    r"(?P<type>[A-Z]+)(?:\((?P<length>\d+)\))?(?P<tail>[^;]*);",
    re.IGNORECASE | re.MULTILINE,
)


def _migration_columns() -> dict[str, tuple[str, int | None, bool]]:
    body = MIGRATION_PATH.read_text()
    upgrade = body.split("def upgrade")[1].split("def downgrade")[0]
    out: dict[str, tuple[str, int | None, bool]] = {}
    for m in _MIGRATION_COLUMN_RE.finditer(upgrade):
        length_match = re.search(r"length\s*=\s*(\d+)", m.group("args"))
        out[m.group("name")] = (
            _type_family(m.group("type")),
            int(length_match.group(1)) if length_match else None,
            m.group("nullable") == "True",
        )
    return out


def _sql_twin_columns() -> dict[str, tuple[str, int | None, bool]]:
    body = SQL_TWIN_PATH.read_text()
    # Strip comment lines so the rationale block can mention DDL freely.
    statements = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("--")
    )
    out: dict[str, tuple[str, int | None, bool]] = {}
    for m in _SQL_COLUMN_RE.finditer(statements):
        tail = m.group("tail").upper()
        out[m.group("name")] = (
            _type_family(m.group("type")),
            int(m.group("length")) if m.group("length") else None,
            "NOT NULL" not in tail,
        )
    return out


def test_migration_and_sql_twin_agree():
    """The two artifacts that can silently diverge.

    The Alembic revision runs on CI and local databases; the SQL twin is applied
    by hand to the shared remote MySQL, whose lineage this repo does not
    contain. If the twin drifts, production 500s while CI stays green.
    """
    migration = _migration_columns()
    twin = _sql_twin_columns()

    assert set(migration) == set(ANALYTICS_COLUMNS), migration
    assert set(twin) == set(ANALYTICS_COLUMNS), twin
    assert migration == twin, (
        "the Alembic revision and the hand-applied SQL twin describe different "
        f"columns:\n  migration={migration}\n  twin={twin}"
    )

    for name, (family, length, _nullable) in migration.items():
        expected_family, expected_length = ANALYTICS_COLUMNS[name]
        assert (family, length) == (expected_family, expected_length), name


def test_migration_and_twin_declare_nullable_with_no_default():
    for source in (_migration_columns(), _sql_twin_columns()):
        for name, (_family, _length, nullable) in source.items():
            assert nullable is True, f"{name} must be nullable"

    twin_sql = "\n".join(
        line
        for line in SQL_TWIN_PATH.read_text().splitlines()
        if not line.strip().startswith("--")
    )
    assert "DEFAULT" not in twin_sql.upper(), (
        "no default is allowed: a defaulted column asserts a fact nobody entered"
    )
    # The remote is on a different lineage, so the generated version stamp must
    # have been stripped — running it would move the remote's alembic pointer.
    assert "alembic_version" not in twin_sql.lower()


def test_migration_chains_onto_the_current_head():
    body = MIGRATION_PATH.read_text()
    assert 'revision = "f4c28d19ab60"' in body
    down = re.search(r'down_revision\s*=\s*"(\w+)"', body).group(1)
    versions = MIGRATION_PATH.parent
    assert any(down in p.name for p in versions.glob("*.py")), (
        f"down_revision {down} does not exist — the lineage is broken"
    )


# ---------------------------------------------------------------------------
# 8. The wire contract: the fields survive an actual HTTP round-trip
# ---------------------------------------------------------------------------

def test_http_create_and_patch_round_trip(client: TestClient):
    headers = {"Authorization": f"Bearer {get_test_admin_token(client)}"}
    sku = _new_sku()
    try:
        resp = client.post(
            "/api/v1/products",
            headers=headers,
            json={
                "sku": sku,
                "name": f"HTTP probe {sku}",
                "price": "199.00",
                "stock": 4,
                "reorder_point": 0,
                "hsn_code": "0904",
                "brand": "  Wellvia  ",
                "shelf_life_days": 365,
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["reorder_point"] == 0
        assert created["hsn_code"] == "0904"
        assert created["brand"] == "Wellvia"
        assert created["shelf_life_days"] == 365

        # PUBLIC detail route: hsn_code and brand are shopper-safe, the two
        # ops fields must NOT be here (see ProductOpsFields).
        fetched = client.get(f"/api/v1/products/{created['id']}").json()
        assert fetched["hsn_code"] == "0904"
        assert fetched["brand"] == "Wellvia"
        assert "reorder_point" not in fetched, (
            "reorder_point leaked to the anonymous storefront route"
        )
        assert "shelf_life_days" not in fetched

        # ...and the admin read route still returns all four.
        admin_view = client.get(
            f"/api/v1/products/{created['id']}/admin", headers=headers
        ).json()
        assert admin_view["reorder_point"] == 0
        assert admin_view["shelf_life_days"] == 365

        patched = client.patch(
            f"/api/v1/products/{created['id']}",
            headers=headers,
            json={"reorder_point": None, "brand": ""},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["reorder_point"] is None
        assert patched.json()["brand"] is None
        assert patched.json()["hsn_code"] == "0904", "an omitted field must not change"
    finally:
        _purge_sandbox()


@pytest.mark.parametrize(
    "patch",
    [
        {"hsn_code": "09041"},
        {"hsn_code": "12A4"},
        {"hsn_code": ""},
        {"hsn_code": "090411101"},
        {"reorder_point": -1},
        {"shelf_life_days": 0},
        {"shelf_life_days": -5},
        {"brand": "b" * 121},
    ],
)
def test_http_rejects_invalid_values(client: TestClient, patch: dict):
    headers = {"Authorization": f"Bearer {get_test_admin_token(client)}"}
    sku = _new_sku()
    try:
        resp = client.post(
            "/api/v1/products",
            headers=headers,
            json={"sku": sku, "name": f"Bad {sku}", "price": "199.00", **patch},
        )
        assert resp.status_code == 422, resp.text
        # And nothing was written.
        db = SessionLocal()
        try:
            assert (
                db.execute(
                    text("SELECT COUNT(*) FROM products WHERE sku = :sku"), {"sku": sku}
                ).scalar_one()
                == 0
            )
        finally:
            db.close()
    finally:
        _purge_sandbox()
