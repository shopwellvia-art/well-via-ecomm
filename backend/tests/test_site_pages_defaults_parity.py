"""Backend half of the site-page defaults parity guard.

DEFAULT_SITE_PAGES (backend) and SITE_PAGES_DEFAULTS (frontend) both carry the
same customer-visible default copy, and the backend copy wins the frontend's
merge — so an edit to one side only silently blanks or staleness-poisons the
live page. The shared keys are pinned in
``tests/fixtures/site_pages_shared_defaults.json``; this test holds the backend
to it, ``defaultsParity.test.js`` holds the frontend to it. Drift on either
side fails that side's CI.

Intentionally a SUBSET check: only the content both sides define is pinned.
Backend-only structure (methods, form, offices, other pages) is free to evolve
without touching the fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.schemas.site_pages import DEFAULT_SITE_PAGES, AboutPage, ContactPage

FIXTURE = Path(__file__).parent / "fixtures" / "site_pages_shared_defaults.json"

# Validate through the pydantic models — the API path does the same, and the
# models inject schema-level defaults (e.g. AboutPage.story_label) that the
# raw DEFAULT_SITE_PAGES dict legitimately omits. What we pin is what the API
# SERVES, not the dict literal.
_PAGE_MODELS = {"about": AboutPage, "contact": ContactPage}


def _shared_subset() -> dict:
    data = json.loads(FIXTURE.read_text())
    data.pop("_comment", None)
    return data


def test_backend_defaults_match_shared_fixture() -> None:
    shared = _shared_subset()
    for page_key, expected in shared.items():
        served = _PAGE_MODELS[page_key].model_validate(
            DEFAULT_SITE_PAGES[page_key]
        ).model_dump()
        for field, value in expected.items():
            assert served[field] == value, (
                f"served default for {page_key}.{field} drifted from "
                f"tests/fixtures/site_pages_shared_defaults.json — update "
                f"backend, frontend and fixture in one commit"
            )
