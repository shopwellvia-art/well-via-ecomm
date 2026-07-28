"""Generate docs/analytics/VIEW_STATUS.md — the living 73-view status matrix.

`VIEW_STATUS.md` is the artifact the implementation plan (P1.6) calls the
contract: one row per view, every view accounted for, and — crucially — an
honest `State` and `Limitation` for the ones that cannot show real data yet. It
is what any later discussion means by "view 41" or "the gated views".

The matrix is derived from `app/services/analytics/registry.py`, so it can never
disagree with the code. Regenerate it whenever the registry changes::

    python backend/scripts/gen_view_status.py

The Backend / Frontend / Tests columns are progress tracking, not registry data.
They are seeded here (see the convention in the generated header) and updated as
work lands — which means they are re-seeded on every regeneration. Progress that
must survive belongs in the registry (`state`), not in this file.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Make the `app` package importable when run as a plain script.
BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.analytics.registry import MODULES  # noqa: E402
from app.services.analytics.types import (  # noqa: E402
    GATED_STATES,
    AnalyticsModuleDefinition,
    AnalyticsViewDefinition,
    ViewState,
)

GENERATOR = "backend/scripts/gen_view_status.py"
REGENERATE_COMMAND = "python backend/scripts/gen_view_status.py"
OUTPUT_PATH = REPO_ROOT / "docs" / "analytics" / "VIEW_STATUS.md"

COLUMNS = (
    "#",
    "Module",
    "View",
    "Route",
    "Sources",
    "Permission",
    "State",
    "Requires",
    "Backend",
    "Frontend",
    "Tests",
    "Limitation",
)

#: Work-tracking placeholder for a view that still needs resolver / UI / test work.
TODO = "TODO"
#: A gated view needs no resolver, no chart wiring and no data test — the gated
#: shell is rendered from the registry alone, so there is nothing to track.
NOT_APPLICABLE_CELL = "—"

STATE_ORDER: tuple[ViewState, ...] = (
    ViewState.LIVE,
    ViewState.PARTIAL,
    ViewState.INTEGRATION_REQUIRED,
    ViewState.FEATURE_REQUIRED,
    ViewState.BLOCKED_BY_MISSING_SOURCE,
    ViewState.NOT_APPLICABLE,
)

STATE_MEANING: dict[ViewState, str] = {
    ViewState.LIVE: "Real data, documented formula, source + freshness shown.",
    ViewState.PARTIAL: "Real data with a stated limitation.",
    ViewState.INTEGRATION_REQUIRED: "Needs an external source that is not connected.",
    ViewState.FEATURE_REQUIRED: "Needs a business capability this system does not have.",
    ViewState.BLOCKED_BY_MISSING_SOURCE: "Source exists in principle but is unusable here.",
    ViewState.NOT_APPLICABLE: "The concept does not apply to this deployment.",
}


def _cell(text: str) -> str:
    """Make a value safe inside a Markdown table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ").strip() or "—"


def _route(module: AnalyticsModuleDefinition, view: AnalyticsViewDefinition) -> str:
    return f"/admin/analytics/{module.slug}/{view.slug}"


def _progress_cells(view: AnalyticsViewDefinition) -> tuple[str, str, str]:
    """Backend / Frontend / Tests seeds for one view."""
    if view.state in GATED_STATES:
        # Nothing to build: the gated shell comes straight from the registry.
        return (NOT_APPLICABLE_CELL, NOT_APPLICABLE_CELL, NOT_APPLICABLE_CELL)
    return (TODO, TODO, TODO)


def _row(module: AnalyticsModuleDefinition, view: AnalyticsViewDefinition) -> str:
    backend, frontend, tests = _progress_cells(view)
    values = (
        str(view.number),
        _cell(module.name),
        _cell(view.name),
        f"`{_route(module, view)}`",
        _cell(", ".join(s.value for s in view.sources)),
        f"`{view.permission}`",
        f"**{view.state.value}**",
        _cell(", ".join(c.value for c in view.requires)),
        backend,
        frontend,
        tests,
        _cell(view.limitation),
    )
    return "| " + " | ".join(values) + " |"


def _summary_table(views: list[tuple[AnalyticsModuleDefinition, AnalyticsViewDefinition]]) -> list[str]:
    counts = Counter(view.state for _, view in views)
    total = len(views)
    lines = [
        "| State | Views | Share | Meaning |",
        "| --- | ---: | ---: | --- |",
    ]
    for state in STATE_ORDER:
        count = counts.get(state, 0)
        share = f"{(count / total * 100):.0f}%" if total else "0%"
        lines.append(f"| **{state.value}** | {count} | {share} | {STATE_MEANING[state]} |")
    lines.append(f"| **Total** | **{total}** | 100% | |")

    unknown = sorted(set(counts) - set(STATE_ORDER), key=lambda s: s.value)
    for state in unknown:  # pragma: no cover - guards a future ViewState member
        lines.append(f"| **{state.value}** | {counts[state]} | | (unclassified state) |")
    return lines


def _module_summary(modules: tuple[AnalyticsModuleDefinition, ...]) -> list[str]:
    lines = [
        "| # | Module | Slug | Views | Permission | Default view |",
        "| ---: | --- | --- | ---: | --- | --- |",
    ]
    for module in modules:
        lines.append(
            f"| {module.number} | {_cell(module.name)} | `{module.slug}` | "
            f"{len(module.views)} | `{module.permission}` | `{module.default_view_slug}` |"
        )
    return lines


def render() -> str:
    pairs: list[tuple[AnalyticsModuleDefinition, AnalyticsViewDefinition]] = [
        (module, view) for module in MODULES for view in module.views
    ]
    pairs.sort(key=lambda pair: pair[1].number)

    lines: list[str] = [
        "<!-- GENERATED FILE — DO NOT EDIT BY HAND -->",
        "",
        "# Analytics — view status matrix",
        "",
        "> **⚠️ THIS FILE IS GENERATED. Do not edit it by hand.**",
        f"> It is produced from `backend/app/services/analytics/registry.py` by `{GENERATOR}`.",
        "> Any manual edit is lost on the next regeneration — change the registry instead.",
        "",
        "> **`VIEW_STATUS.md` is the contract referred to throughout the analytics",
        "> implementation plan.** Every one of the 73 views appears below exactly once,",
        "> with an honest state. A view that cannot show real data says so here and in",
        "> the UI; it is never quietly rendered as an empty chart or a false zero.",
        "",
        "Regenerate with:",
        "",
        "```bash",
        REGENERATE_COMMAND,
        "```",
        "",
        "## Summary by state",
        "",
        *_summary_table(pairs),
        "",
        "## Modules",
        "",
        *_module_summary(MODULES),
        "",
        "## Column conventions",
        "",
        "- **#** — the stable 1..73 view number from the product brief. It never changes,",
        "  even if a view is renamed or moves module. This is what every discussion,",
        "  ticket and test refers to.",
        "- **Route** — `/admin/analytics/{module_slug}/{view_slug}`, resolved by one",
        "  metadata-driven page component, not 73 hand-written pages.",
        "- **Sources** — where the numbers come from. Any view carrying financial",
        "  figures must include `internal_db`; GA4 is never the accounting value.",
        "- **State** — the *ceiling* declared by the registry. A runtime probe may",
        "  downgrade it (LIVE → PARTIAL when a rollup has no rows yet) but never",
        "  upgrade it.",
        "- **Requires** — the capabilities a gated or partial view is waiting on. This is",
        "  what the gated UI shows the admin instead of an unexplained empty state.",
        f"- **Backend / Frontend / Tests** — work tracking, updated as work lands. `{TODO}`",
        "  means the resolver / UI wiring / test still has to be written.",
        f"  `{NOT_APPLICABLE_CELL}` means there is nothing to do: the view is gated",
        f"  ({', '.join(s.value for s in STATE_ORDER if s in GATED_STATES)}), so it needs",
        "  no resolver work — its shell is rendered from the registry alone. These three",
        "  columns are re-seeded on every regeneration; durable status belongs in the",
        "  registry's `state` field, not here.",
        "- **Limitation** — the one line shown to the admin on a gated or partial view.",
        "  Every non-LIVE view must have one; the registry test enforces it.",
        "",
        "## All views",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    lines.extend(_row(module, view) for module, view in pairs)
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = render()
    OUTPUT_PATH.write_text(content, encoding="utf-8")
    view_count = sum(len(module.views) for module in MODULES)
    print(
        f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)} "
        f"({len(MODULES)} modules, {view_count} views)"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
