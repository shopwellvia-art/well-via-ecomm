"""The invariants that make a `ViewState` mean something.

`tests/unit/test_analytics_registry.py` already guards the registry's *shape*
(73 views, unique slugs, valid enums) and one direction of honesty: a non-LIVE
view must say what it needs. This module guards the other direction, which is
the one that actually shipped a lie.

The bug this exists to prevent
------------------------------
View 66 (`control-centre/experiment-and-ab-testing`) was declared `LIVE`. LIVE
is not a label — `GATED_STATES` in `types.py` is what the frontend consults to
decide whether to issue a network request at all, so a LIVE view *fetches* and
renders a data page with KPI cards and a chart. View 66 fetched
`special.experiment_results`, whose entire body is::

    return not_configured(...)

There is no experiment framework in this deployment: no assignment service, no
exposure logging, no variant storage. No table in the schema has an experiment
or variant column, and `cart_events.meta` — the one free-form field that could
carry an arm — is never written with one. `experiment_id` exists in the Clarity
tag allowlist (`frontend/src/features/tracking/clarity.js`) and nothing calls
`setClarityTag` with it; a declared parameter that nothing populates is not a
data source.

So the view promised per-variant conversion, AOV and revenue that could not
exist. Every structural test passed, because none of them asked the question
"can this view actually produce a number?".

The four invariants
-------------------
1. **A LIVE view must have a way to produce data** — a `params` binding or a
   bespoke component. And, the part that would have caught view 66: if it
   dispatches to a custom function, that function must not be an unconditional
   `not_configured`. A resolver that can only ever refuse is not a data source,
   and wiring one behind a LIVE state is how a gated view disguises itself.
2. **Every non-LIVE view declares a non-empty `requires[]`**, so the gated UI
   names the missing capability instead of rendering a blank panel. Also
   asserted in `test_analytics_registry.py`; restated here because it is half
   of one rule and splitting it across two files is how the other half gets
   dropped.
3. **Every declared `permission` exists** in `all_permission_names()`, or the
   view is unreachable by any role.
4. **A NOT_APPLICABLE view explains *why* the concept does not apply**, not
   merely that it does not. NOT_APPLICABLE is the one state that will never
   become LIVE without a change to the business model; if it does not say what
   that change would be, it is indistinguishable from a feature nobody built.

Known gaps
----------
`KNOWN_GAPS` carries views that fail an invariant and are owned by somebody
else right now. They are `xfail(strict=True)`, so the suite is honest in both
directions: it does not go green by pretending the gap is fine, and the moment
the owner fixes the view the XPASS turns the entry into a failure that forces
the marker's removal. The assertion itself is never weakened for them.

Pure logic — no database, no network, no fixtures::

    docker exec wvana-py python -m pytest tests/test_analytics_view_state_honesty.py -q
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap

import pytest

from app.services.analytics.registry import MODULES
from app.services.analytics.resolvers.special import CUSTOM_FUNCTIONS
from app.services.analytics.types import (
    GATED_STATES,
    AnalyticsViewDefinition,
    ViewState,
)
from app.services.permissions_registry import all_permission_names

# ---------------------------------------------------------------------------
# Views under test
# ---------------------------------------------------------------------------

ALL_VIEWS: tuple[AnalyticsViewDefinition, ...] = tuple(
    view for module in MODULES for view in module.views
)


def _ids(views: tuple[AnalyticsViewDefinition, ...]) -> list[str]:
    return [f"{v.number:02d}-{v.slug}" for v in views]


#: Views that fail an invariant below and belong to another workstream right
#: now. Keyed by `(test, view number)` — deliberately not by view alone, so a
#: listed view still has to satisfy every *other* invariant here. Marking a
#: whole view "known bad" is how a second, unrelated defect hides behind the
#: first.
#:
#: Rules for this dict:
#:   * `strict=True` — a fixed view XPASSes, which fails, which forces the entry
#:     out. Nothing rots in here silently.
#:   * Only ever an *ownership* excuse, never a correctness one. If a view
#:     genuinely may sit in this shape, that belongs in the invariant as a
#:     documented carve-out, not here.
#: Two entries were here and are already gone, which is the mechanism working:
#: view 42 (LIVE with no binding) and view 71 (PARTIAL with no binding) were
#: both fixed by their owners while this file was being written, and strict
#: xfail turned each into an XPASS that had to be deleted rather than a green
#: tick nobody looked at.
KNOWN_GAPS: dict[tuple[str, int], str] = {
    # The view-30 entry left this dict the way the mechanism intends: the view
    # was PARTIAL with an empty `params`, and binding it (resolvers/turnover.py,
    # via the repository's `avg:` projection) turned the strict xfail into an
    # XPASS that forced this removal. The state stayed PARTIAL — the wiring
    # changed, the promise did not.
    # The view-58 entry left this dict the way the mechanism intends: the view
    # moved PARTIAL -> FEATURE_REQUIRED (it fetched with nothing to read, and
    # the honest subset it could have read turned out to be view 57's attach
    # rate under another name), the invariant passed, and strict xfail forced
    # the entry out. See the view-58 comment in registry.py and
    # tests/test_analytics_upsell.py.
}


def _cases(
    views: tuple[AnalyticsViewDefinition, ...], test_key: str
) -> list[object]:
    """Parametrize entries, xfailed only where KNOWN_GAPS names this test."""
    out: list[object] = []
    for view in views:
        reason = KNOWN_GAPS.get((test_key, view.number))
        if reason is None:
            out.append(view)
        else:
            out.append(
                pytest.param(view, marks=pytest.mark.xfail(strict=True, reason=reason))
            )
    return out


# ---------------------------------------------------------------------------
# Invariant 1 — a LIVE view can actually produce data
# ---------------------------------------------------------------------------

LIVE_VIEWS: tuple[AnalyticsViewDefinition, ...] = tuple(
    v for v in ALL_VIEWS if v.state is ViewState.LIVE
)


def _refuses_unconditionally(fn_name: str) -> bool:
    """True if this custom function can only ever return `not_configured`.

    Decided from the AST rather than by calling it: calling needs a
    `ResolverContext` and a database, and the property being asserted is
    static. A function qualifies when *every* `return` it contains is a direct
    `not_configured(...)` call **and** none of them sit inside a branch or loop
    — i.e. there is no input for which it does anything else.

    Deliberately conservative. A function that returns `not_configured` only
    under an `if` is a legitimate partial resolver (it refuses when a source is
    missing and answers otherwise) and is not flagged.
    """
    fn = CUSTOM_FUNCTIONS.get(fn_name)
    if fn is None:
        return False
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, SyntaxError, TypeError):  # pragma: no cover - source always available
        return False

    func = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)),
        None,
    )
    if func is None:  # pragma: no cover
        return False

    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
    if not returns:
        return False

    def _is_not_configured(node: ast.Return) -> bool:
        call = node.value
        if not isinstance(call, ast.Call):
            return False
        target = call.func
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        return name == "not_configured"

    if not all(_is_not_configured(r) for r in returns):
        return False

    # Every return refuses. Only unconditional if none is nested in a branch.
    branching = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.Match)
    nested: set[int] = set()
    for node in ast.walk(func):
        if isinstance(node, branching):
            nested.update(id(n) for n in ast.walk(node) if isinstance(n, ast.Return))
    return not any(id(r) in nested for r in returns)


@pytest.mark.parametrize(
    "view", _cases(LIVE_VIEWS, "live_view_can_produce_data"), ids=_ids(LIVE_VIEWS)
)
def test_live_view_can_produce_data(view: AnalyticsViewDefinition):
    """LIVE makes the frontend fetch. Something must be on the other end.

    `GATED_STATES` does not contain LIVE, so the client issues a request and
    renders a data page. A LIVE view with no `params` binding and no bespoke
    component has nothing to answer it with, and the admin gets an empty
    dashboard with no explanation — strictly worse than a gated view, which at
    least says what is missing.
    """
    assert view.params or view.bespoke, (
        f"View {view.number} ({view.slug}) is LIVE but declares neither `params` nor "
        "`bespoke`, so no resolver has anything to read and the view can only render "
        "empty. Either bind it to a rollup, or give it the state that matches what it "
        "can show and a `requires` naming what is missing."
    )


@pytest.mark.parametrize(
    "view",
    _cases(LIVE_VIEWS, "live_view_does_not_dispatch_to_a_permanent_refusal"),
    ids=_ids(LIVE_VIEWS),
)
def test_live_view_does_not_dispatch_to_a_permanent_refusal(
    view: AnalyticsViewDefinition,
):
    """The view-66 regression, stated directly.

    A custom function whose only behaviour is `return not_configured(...)` is a
    gated view wearing a LIVE label: the fetch happens, costs a round trip, and
    comes back with "not configured" rendered inside a page that promised data.
    The honest encoding is a gated state plus a `requires`, which stops the
    request at the client and names the missing capability.

    Scoped to LIVE on purpose. Three custom functions currently refuse on every
    path — `experiment_results` (view 66), `journey_paths` (view 65) and
    `demand_forecast` (view 31) — and only a LIVE view makes that a lie. Views
    65 and 66 are gated, so the client never calls them. View 31 is PARTIAL, so
    it does fetch, but its refusal is not the same object: it reads the
    inventory watermark first and returns the real day counts it has against the
    number it needs, under a state that already says "incomplete" and a
    `requires` that names the ledger. That is a progress report, not a page
    pretending to be data. Do not "fix" view 31 by deleting that.
    """
    fn_name = view.params.get("fn") or view.bespoke
    if not fn_name:
        return  # covered by test_live_view_can_produce_data
    assert not _refuses_unconditionally(fn_name), (
        f"View {view.number} ({view.slug}) is LIVE and dispatches to custom function "
        f"{fn_name!r}, which returns `not_configured` on every path — it cannot ever "
        "produce data. LIVE makes the client fetch it anyway. Move the view to the "
        "state that matches (FEATURE_REQUIRED / INTEGRATION_REQUIRED) and declare a "
        "`requires`, so the UI explains itself instead of fetching a refusal."
    )


# ---------------------------------------------------------------------------
# Invariant 2 — every non-LIVE view says what it is waiting on
# ---------------------------------------------------------------------------

NON_LIVE_VIEWS: tuple[AnalyticsViewDefinition, ...] = tuple(
    v for v in ALL_VIEWS if v.state is not ViewState.LIVE
)


@pytest.mark.parametrize("view", NON_LIVE_VIEWS, ids=_ids(NON_LIVE_VIEWS))
def test_non_live_view_declares_what_it_requires(view: AnalyticsViewDefinition):
    """A view that cannot show everything must name the capability it needs.

    This is the acceptance criterion the plan states, asserted rather than
    assumed. `requires` is what the gated UI turns into "connect GA4" or "no
    recommendation engine is running" — without it the admin sees a blank panel
    and concludes the product is broken.
    """
    assert view.requires, (
        f"View {view.number} ({view.slug}) is {view.state.value} but declares an empty "
        "`requires`. Name the capability it is waiting on — that string is the entire "
        "content of the gated panel the admin will actually see."
    )
    assert view.limitation.strip(), (
        f"View {view.number} ({view.slug}) is {view.state.value} with an empty "
        "`limitation`. `requires` names the capability; `limitation` is the sentence "
        "explaining what it means for this view."
    )


@pytest.mark.parametrize(
    "view",
    _cases(NON_LIVE_VIEWS, "gated_view_is_not_silently_fetchable"),
    ids=_ids(NON_LIVE_VIEWS),
)
def test_gated_view_is_not_silently_fetchable(view: AnalyticsViewDefinition):
    """PARTIAL fetches; the other four non-LIVE states must not.

    Guards the assumption the whole `requires` mechanism rests on: that a state
    the registry treats as "explain, do not query" is in fact in `GATED_STATES`,
    which is the set the client checks.
    """
    if view.state is ViewState.PARTIAL:
        assert view.params or view.bespoke, (
            f"View {view.number} ({view.slug}) is PARTIAL, which still fetches, but has "
            "neither `params` nor `bespoke` to answer with. PARTIAL means reduced data, "
            "not absent data."
        )
        return
    assert view.state in GATED_STATES, (
        f"View {view.number} ({view.slug}) is {view.state.value}, which is neither LIVE "
        "nor PARTIAL nor in GATED_STATES. The client decides whether to issue a request "
        "from GATED_STATES alone, so this view would fetch while presenting itself as "
        "unavailable."
    )


# ---------------------------------------------------------------------------
# Invariant 3 — every declared permission exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("view", ALL_VIEWS, ids=_ids(ALL_VIEWS))
def test_view_permission_is_a_real_permission(view: AnalyticsViewDefinition):
    """A permission no role can hold makes the view unreachable, not secure."""
    known = set(all_permission_names())
    assert view.permission in known, (
        f"View {view.number} ({view.slug}) is gated on permission "
        f"{view.permission!r}, which is not in the permissions registry. No role can "
        "ever be granted it, so the view 403s for everyone including the owner."
    )


def test_module_permissions_are_real_permissions():
    known = set(all_permission_names())
    bad = sorted(
        f"{m.slug} -> {m.permission}" for m in MODULES if m.permission not in known
    )
    assert not bad, (
        "Modules declare permissions that do not exist, so their whole sidebar "
        "section is unreachable: " + "; ".join(bad)
    )


# ---------------------------------------------------------------------------
# Invariant 4 — NOT_APPLICABLE explains why
# ---------------------------------------------------------------------------

NOT_APPLICABLE_VIEWS: tuple[AnalyticsViewDefinition, ...] = tuple(
    v for v in ALL_VIEWS if v.state is ViewState.NOT_APPLICABLE
)

#: Phrases that introduce a *reason*. A limitation containing none of these is
#: almost certainly restating the state ("this view is not applicable") rather
#: than giving the fact about this deployment that makes it so.
_REASON_MARKERS = re.compile(
    r"\b(because|since|there (is|are) no|this is a|only one|"
    r"single-\w+|does not have|has no|no \w+ (exist|are|is)|until|would require)\b",
    re.IGNORECASE,
)

#: A limitation that only says the concept does not apply, with no cause.
_TAUTOLOGY = re.compile(
    r"^\W*(this view is |it is |)?(not applicable|n/?a|does not apply)\W*$",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    "view", NOT_APPLICABLE_VIEWS, ids=_ids(NOT_APPLICABLE_VIEWS)
)
def test_not_applicable_view_explains_why_the_concept_does_not_apply(
    view: AnalyticsViewDefinition,
):
    """NOT_APPLICABLE is the only state that is not a promise of "later".

    Unlike FEATURE_REQUIRED, it will never become LIVE without a change to the
    business model. That is only useful to a reader if the view names the fact
    about *this* deployment that makes the concept meaningless — "this is a
    single-store deployment" — rather than the tautology "this does not apply".
    Without the cause, the admin cannot tell an inapplicable view from an
    unbuilt one and reasonably files a bug.
    """
    limitation = view.limitation.strip()
    assert limitation, (
        f"View {view.number} ({view.slug}) is NOT_APPLICABLE with an empty "
        "`limitation`. This state renders as a permanent dead end; say what makes it "
        "one."
    )
    assert not _TAUTOLOGY.match(limitation), (
        f"View {view.number} ({view.slug}) explains NOT_APPLICABLE with "
        f"{limitation!r}, which only restates the state. Name the property of this "
        "deployment that makes the concept meaningless."
    )
    assert _REASON_MARKERS.search(limitation), (
        f"View {view.number} ({view.slug}) is NOT_APPLICABLE and its `limitation` "
        f"({limitation!r}) states no cause. Write why the concept does not apply here "
        "— e.g. \"this is a single-store deployment, so there are no branches to "
        "compare\" — not just that it does not."
    )
    assert view.requires, (
        f"View {view.number} ({view.slug}) is NOT_APPLICABLE but declares no "
        "`requires`. Even a permanent dead end should name the capability whose "
        "absence causes it, so the reason is machine-readable and not only prose."
    )


# ---------------------------------------------------------------------------
# The two views this module was written for
# ---------------------------------------------------------------------------


def _view(number: int) -> AnalyticsViewDefinition:
    return next(v for v in ALL_VIEWS if v.number == number)


def test_view_66_experiments_is_gated_because_no_experiment_framework_exists():
    """Pin the fix, with the evidence, so it cannot silently revert.

    Verified against the live schema at the time of the change: no experiment
    or variant table, no experiment/variant column on any of the 73 tables, and
    no `cart_events` row carrying an arm in `meta`. `experiment_id` appears only
    in the Clarity tag allowlist, which is write-only to Microsoft and never
    read back — and `setClarityTag` is never called with it in any case.
    """
    view = _view(66)
    assert view.state is not ViewState.LIVE, (
        "View 66 is LIVE again. Nothing assigns visitors to variants, so a per-variant "
        "table cannot be populated; LIVE makes the client fetch and render it anyway."
    )
    assert view.state in GATED_STATES, (
        f"View 66 is {view.state.value}, which still fetches. With no assignment, no "
        "exposure logging and no variant storage there is nothing to fetch."
    )
    assert view.requires, "View 66 must name the capability it is missing."
    assert not view.params, (
        "View 66 declares a `params` binding. There is no experiment rollup to bind "
        "to; if one now exists, this test should be rewritten around it rather than "
        "deleted."
    )


def test_view_66_limitation_names_significance_not_just_missing_data():
    """An A/B result without a significance test is a coin flip with a winner.

    The three infrastructure gaps (assignment, exposure, storage) are the
    obvious ones and would be fixed first. The statistical requirement is the
    one that gets skipped, because a bar chart of two conversion rates looks
    finished without it. Keeping it in the limitation means whoever ungates this
    view has been told, in the artifact they must edit to do so.
    """
    limitation = _view(66).limitation.lower()
    for term in ("assignment", "exposure", "variant storage", "significance"):
        assert term in limitation, (
            f"View 66's limitation does not mention {term!r}. All four are required "
            "before a number on this view means anything, and the limitation is the "
            "only place a reader is told so."
        )
    assert "sample size" in limitation, (
        "View 66's limitation does not mention a minimum sample size. Significance on "
        "a handful of sessions is noise with a p-value attached."
    )


def test_view_46_recommendations_is_not_bound_to_the_basket_rollup():
    """Co-occurrence is not recommender performance, and 57 already shows it.

    `agg_basket_pair_daily` has landed, and it is the nearest thing in the
    schema to this view's subject — which is exactly why the mistake is
    available. It records which products customers bought together unprompted.
    View 46 promises impressions of a recommendation slot, clicks on it, and
    revenue attributed to it: measurements of what a recommender *caused*.
    There is no recommender and no slot instrumentation, so binding the pair
    rollup here would publish observation under three headings that promise
    causation, and would duplicate view 57, which is LIVE on that rollup and is
    where "frequently bought together" belongs.
    """
    view = _view(46)
    assert view.state is ViewState.FEATURE_REQUIRED, (
        f"View 46 is {view.state.value}. No recommendation engine serves a slot and "
        "nothing logs an impression or a click on one, so there is no recommender "
        "performance to report at any state above FEATURE_REQUIRED."
    )
    assert not view.params, (
        "View 46 is bound to a rollup. If that is `agg_basket_pair_daily`, it measures "
        "co-occurrence, not recommender performance — view 57 already reports it "
        "honestly under a name that matches."
    )
    assert view.requires, "View 46 must name the capability it is missing."
