"""Storefront company/content pages — a single-row JSON document table.

One row holds the entire "company pages" document (About, Contact, Careers,
Wellvia Stories, Press, Corporate Information). The service layer reads this row
and merges in application defaults when the row is absent, so the table starts
empty — exactly like `footer_config`.
"""
from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IDMixin, TimestampMixin


class SitePages(Base, IDMixin, TimestampMixin):
    __tablename__ = "site_pages"

    # The complete company-pages document. `default=dict` fires in Python on
    # every ORM INSERT; MySQL 8.0 rejects server_default on JSON columns, so
    # raw SQL inserts must supply the value explicitly.
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
