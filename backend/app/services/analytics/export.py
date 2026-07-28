"""CSV export for analytics tables — the first streamed response in this app.

There is no other CSV code in this codebase, so every rule a CSV needs is
established here rather than inherited. Four of them carry real weight.

**A CSV is a program.** Excel, LibreOffice and Google Sheets all evaluate a cell
that begins with ``=``, ``+``, ``-`` or ``@`` as a formula, and ``DDE``/``WEBSERVICE``
turn that into remote command execution on the machine of whoever opens the
file. Nothing in this application escapes an attacker-controlled string on its
way into a spreadsheet — but a product name, a coupon code, a courier name and a
customer's city all reach this exporter, and every one of them is written by
somebody outside the finance team. ``sanitise_cell`` prefixes any triggering
value with a single quote AND quotes the field, which neutralises the formula
while leaving the text readable. This is not defence in depth; for CSV it is the
whole defence.

**Truncation is never silent.** Two bounds apply: 50 000 rows, and ~45 seconds
of wall clock (nginx's ``proxy_read_timeout`` is 60s, and a response killed
mid-stream arrives as a *valid-looking* short CSV). When either bites, the file
ends with an explicit final row saying so. A CSV that quietly stops at row
50 000 is worse than an error: it looks complete, it reconciles against nothing,
and the person who acts on it has no way to discover what was missing.

**The file says what it is.** Every export opens with a header block echoing the
view, the resolved window, the timezone, the comparison basis and every filter
that was actually applied. A CSV outlives the URL that produced it by years; one
without that block is a grid of numbers with no provenance, and the first
question anyone asks of it — "what period is this?" — is unanswerable.

**Rows are materialised before the response is returned.** The bounds above are
enforced while building, not while streaming, so the audit row can carry the
true row count and the caller learns about truncation from the response rather
than from a file they opened a day later. Streaming then hands the client the
already-bounded lines in chunks, which is what keeps a 50 000-row file off the
single-buffer path.

**An export asks for more rows than a screen, and says so out loud.** The row
ceiling is a property of the *request*, not of the resolver: a browser rendering
50 000 rows is its own outage, so an on-screen request stays clamped at
``SCREEN_ROW_CAP``, while an export is the one caller that legitimately wants
the long tail. ``export_scope()`` below is how a resolver learns which of the
two it is running for. Raising the shared cap instead would let a single
dashboard request pull the whole table, which is exactly the denial of service
the cap exists to prevent.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Iterator, Sequence

from fastapi.responses import StreamingResponse

from app.repositories.analytics_repository import HARD_ROW_CAP as REPOSITORY_ROW_CAP
from app.services.analytics.filters import MAX_LIMIT
from app.services.analytics.types import AnalyticsViewDefinition, TableSpec

#: Hard row cap. Chosen to stay comfortably inside a request worker's memory and
#: well under Excel's own limits, and low enough that a 50 001st row is a signal
#: to narrow the window rather than to raise the cap.
CSV_ROW_CAP = 50_000

#: Wall-clock budget for building the file. nginx's `proxy_read_timeout` is 60s;
#: a build that overruns it is killed mid-write and delivers a truncated file
#: with no truncation marker — the exact failure this module exists to prevent.
CSV_TIME_BUDGET_SEC = 45.0

#: A cell starting with any of these is treated as a formula by every major
#: spreadsheet. TAB / CR / LF are included because they are stripped on paste,
#: which re-exposes whatever character follows them.
FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r", "\n")

#: Prefixing with an apostrophe forces the cell to text. It is displayed as part
#: of the value in some viewers, which is the correct trade: a visible artefact
#: beats a silent execution.
SANITISE_PREFIX = "'"

#: Windows-1252 is still the default for a double-clicked .csv on Windows Excel.
#: The BOM is what makes it read the file as UTF-8, so a customer's name in
#: Devanagari or a ₹ sign survives the round trip.
UTF8_BOM = "\ufeff"

#: Rows per yielded chunk. Small enough that time-to-first-byte stays low,
#: large enough that a 50 000-row file is not 50 000 ASGI messages.
STREAM_CHUNK_ROWS = 500

#: Prefix for the metadata and truncation rows. Not a formula trigger, and
#: recognisable to both a human and a parser that wants to skip them.
COMMENT_PREFIX = "#"

#: Explicit truncation marker. Machine-greppable, human-readable, and it is the
#: last row in the file so it cannot be missed by anyone scrolling to the end.
TRUNCATION_MARKER = f"{COMMENT_PREFIX} TRUNCATED"


# ---------------------------------------------------------------------------
# Row ceilings — one per kind of request
# ---------------------------------------------------------------------------
#: What a *screen* may ask a resolver for. Identical to the bound
#: `AnalyticsFilters.limit` validates against, restated here (rather than only
#: imported at the point of use) because this module is where the two ceilings
#: are compared, and a reader of one needs the other in view.
SCREEN_ROW_CAP = MAX_LIMIT

#: What an *export* may ask a resolver for, in place of `SCREEN_ROW_CAP`.
#:
#: One below the repository's own `HARD_ROW_CAP` rather than equal to it: every
#: resolver fetches `limit + 1` rows so it can *report* a truncated list instead
#: of silently ending at the cap, and `_execute_capped` raises rather than
#: truncates when a limit exceeds its ceiling. Derived from the repository's
#: constant so the two cannot drift apart into a 500 that only ever fires on the
#: largest exports — the requests nobody runs by hand.
#:
#: This is NOT `CSV_ROW_CAP`. `CSV_ROW_CAP` bounds the *file*, and applies to
#: any iterable of rows `build_csv_export` is handed; this bounds one
#: **repository read**, which is the smaller of the two ceilings today. Raising
#: it means giving the repository a paginated or streaming read, not editing
#: this line.
EXPORT_ROW_CAP = REPOSITORY_ROW_CAP - 1

#: True for the duration of one export build, and false everywhere else.
#:
#: A `ContextVar` rather than a field on `AnalyticsFilters` for one decisive
#: reason: `AnalyticsFilters` is bound straight from the query string, so a
#: field on it would be a client-settable `?export=true` that lifts the row cap
#: on any dashboard request — the exact denial of service the cap exists to
#: prevent. Nothing a caller can send reaches this variable; only
#: `export_scope()` sets it, and only `POST /exports` enters that scope.
#:
#: The default is `False`, so a code path that forgets to enter the scope gets
#: the *smaller* ceiling. The failure direction is deliberate: a forgotten
#: signal costs an export some rows, whereas a leaked one costs the database.
#:
#: Context-local, not global: FastAPI runs a sync endpoint in a worker thread
#: with a copy of the caller's context, so the flag cannot leak into a concurrent
#: request the way a module-level boolean would.
_EXPORTING: ContextVar[bool] = ContextVar("analytics_exporting", default=False)


@contextmanager
def export_scope() -> Iterator[None]:
    """Mark everything resolved inside this block as belonging to an export.

    Reset through the token in a `finally`, never assigned back to `False`:
    resetting restores whatever was there before, so nesting is harmless and an
    exception cannot leave the flag stuck on for whatever else runs in this
    context.
    """
    token = _EXPORTING.set(True)
    try:
        yield
    finally:
        _EXPORTING.reset(token)


def is_exporting() -> bool:
    """Whether the work in flight is building an export."""
    return _EXPORTING.get()


def row_cap_for_request() -> int:
    """The row ceiling that applies to the request in flight."""
    return EXPORT_ROW_CAP if _EXPORTING.get() else SCREEN_ROW_CAP


def clamp_row_limit(requested: Any, *, default: int = 20) -> int:
    """Clamp a requested row count to the ceiling for THIS kind of request.

    The single place every resolver applies its row bound. Called rather than
    `min(limit, MAX_LIMIT)` open-coded, so "how many rows may this request ask
    for" has one answer that both callers agree on and neither can drift from.
    """
    return min(int(requested or default), row_cap_for_request())


# ---------------------------------------------------------------------------
# Cell formatting
# ---------------------------------------------------------------------------
def _is_number(value: Any) -> bool:
    """True for values that are numbers, not text that looks like one.

    `bool` is excluded even though it is an `int` subclass: `True` should render
    as `true`, not `1`.
    """
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def sanitise_cell(text: str) -> str:
    """Neutralise a spreadsheet formula in a text cell.

    Exactly the rule in the module docstring: a leading ``=``, ``+``, ``-``,
    ``@``, TAB, CR or LF gets a single-quote prefix. The check is on the *first
    character only* — an embedded ``=`` is inert, and escaping those would
    mangle ordinary values like ``size=XL``.

        >>> sanitise_cell("=cmd|' /C calc'!A0")
        "'=cmd|' /C calc'!A0"
    """
    if text.startswith(FORMULA_TRIGGERS):
        return SANITISE_PREFIX + text
    return text


def render_cell(value: Any) -> tuple[str, bool]:
    """Turn one value into CSV text. Returns (text, must_be_quoted).

    Real numbers pass through unsanitised. This is deliberate and is the one
    place the docstring's rule is narrowed: a `Decimal("-1250.00")` refund is
    not a formula, and prefixing it would turn every negative figure in the file
    into text that no spreadsheet will sum. A *string* `"-1250.00"` — which is
    what an untrusted free-text field would arrive as — is still sanitised, so
    the narrowing costs nothing in safety. The distinction is on the Python
    type, which the client cannot influence.
    """
    if value is None:
        return "", False
    if isinstance(value, bool):
        return ("true" if value else "false"), False
    if _is_number(value):
        return str(value), False
    if isinstance(value, datetime):
        return value.isoformat(), False
    if isinstance(value, date):
        return value.isoformat(), False

    text = str(value)
    safe = sanitise_cell(text)
    # A sanitised cell is force-quoted so the apostrophe cannot be reinterpreted
    # by a lenient parser, and so the field boundary is unambiguous whatever the
    # value contains.
    return safe, safe != text


def csv_field(text: str, *, force_quote: bool = False) -> str:
    """One RFC 4180 field.

    Hand-rolled rather than `csv.writer` because quoting has to be decided *per
    field*: `QUOTE_MINIMAL` would leave a sanitised cell unquoted and
    `QUOTE_ALL` would quote every number in a 50 000-row file for no benefit.
    """
    if force_quote or any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def csv_line(values: Sequence[Any]) -> str:
    """One CSV record, CRLF-terminated per RFC 4180."""
    fields = []
    for value in values:
        text, force = render_cell(value)
        fields.append(csv_field(text, force_quote=force))
    return ",".join(fields) + "\r\n"


def _one_line(value: Any) -> str:
    """Collapse a header value onto one line.

    A newline inside a metadata value would split the header block into rows
    that look like data. Values here are bounded pydantic fields, so this is
    defence in depth rather than a live risk.
    """
    return " ".join(str(value).split())


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
@dataclass
class CsvExport:
    """A built, bounded CSV. Row count is known before anything is streamed."""

    filename: str
    lines: list[str] = field(default_factory=list)
    #: Data rows written. Excludes the header block, the column header and the
    #: truncation marker — it is the number an audit row should carry.
    row_count: int = 0
    truncated: bool = False
    truncation_reason: str = ""
    #: How many cells were formula-neutralised. Surfaced so a spike is visible
    #: in the audit trail rather than only in the file.
    sanitised_cells: int = 0

    @property
    def byte_size(self) -> int:
        return sum(len(line.encode("utf-8")) for line in self.lines)

    def text(self) -> str:
        """The whole file. For tests and small files; the response streams."""
        return "".join(self.lines)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------
def _header_block(
    *,
    view: AnalyticsViewDefinition,
    module_slug: str,
    table_id: str,
    resolved: Any,
    exported_by: str,
    timezone_name: str,
    currency: str,
    availability: str,
    quality: str,
    generated_at: datetime,
) -> list[str]:
    """The self-describing preamble. Two columns: `# key`, value.

    Emitted as real CSV rows rather than free-form comment lines so a
    spreadsheet renders it as a readable block and a parser can skip it on the
    `#` prefix instead of guessing.
    """
    applied = getattr(resolved, "applied", {}) or {}
    ignored = getattr(resolved, "ignored", []) or []

    rows: list[tuple[str, Any]] = [
        ("export", "Wellvia analytics"),
        ("view", f"{view.number}. {view.name}"),
        ("module_slug", module_slug),
        ("view_slug", view.slug),
        ("table", table_id),
        ("generated_at_utc", generated_at.isoformat()),
        ("exported_by", exported_by),
        ("reporting_timezone", timezone_name),
        ("currency", currency),
        ("period", getattr(resolved, "period", "")),
        # Stated as half-open on the face of the file: the alternative is every
        # reader silently assuming the end date is included and being off by a
        # day against the dashboard they are reconciling to.
        ("date_from", getattr(resolved, "date_from", "")),
        ("date_to_exclusive", getattr(resolved, "date_to", "")),
        ("comparison", getattr(resolved, "comparison", "")),
        ("compare_from", getattr(resolved, "compare_from", "") or ""),
        ("compare_to_exclusive", getattr(resolved, "compare_to", "") or ""),
        ("granularity", getattr(resolved, "granularity", "")),
        ("status_basis", getattr(resolved, "status_basis", "")),
        ("row_limit", getattr(resolved, "limit", "")),
        ("availability", availability),
        ("data_quality", quality),
        ("row_cap", CSV_ROW_CAP),
    ]
    if applied:
        rows.append(
            ("filters_applied", "; ".join(f"{k}={v}" for k, v in sorted(applied.items())))
        )
    else:
        rows.append(("filters_applied", "none"))
    if ignored:
        # Named explicitly: a filter this view does not support was NOT applied,
        # and a reader who assumes it was will misread every number below.
        rows.append(("filters_ignored_by_this_view", "; ".join(sorted(ignored))))
    if view.limitation:
        rows.append(("limitation", view.limitation))

    return [
        csv_line([f"{COMMENT_PREFIX} {key}", _one_line(value)]) for key, value in rows
    ]


def _columns(
    table_spec: TableSpec | None, rows: Sequence[dict[str, Any]]
) -> list[tuple[str, str]]:
    """(key, label) pairs, from the registry spec when there is one.

    The registry spec is preferred because it fixes column *order* — a CSV whose
    columns move between exports cannot be diffed against last month's, and dict
    ordering is an implementation detail of whichever resolver produced the row.
    Falling back to the first row's keys keeps a resolver that returns an
    undeclared table exportable rather than erroring.
    """
    if table_spec is not None and table_spec.columns:
        return [(c.key, c.label) for c in table_spec.columns]
    if rows:
        return [(k, k) for k in rows[0].keys()]
    return []


def build_csv_export(
    *,
    view: AnalyticsViewDefinition,
    module_slug: str,
    table_id: str,
    table_spec: TableSpec | None,
    rows: Iterable[dict[str, Any]],
    resolved: Any,
    exported_by: str,
    timezone_name: str,
    currency: str = "INR",
    availability: str = "",
    quality: str = "",
    row_cap: int = CSV_ROW_CAP,
    time_budget_sec: float = CSV_TIME_BUDGET_SEC,
    source_truncated: bool = False,
    source_row_limit: int | None = None,
    generated_at: datetime | None = None,
) -> CsvExport:
    """Build the whole file, bounded by `row_cap` and `time_budget_sec`.

    `rows` is consumed lazily, so a repository that streams does not have to
    materialise everything before the cap applies. The clock starts here, not at
    the request, because the budget exists to protect the *response*, and the
    permission and registry work before this point is measured in microseconds.

    `source_truncated` is the resolver's own verdict, carried on the `TableBlock`
    it produced. It matters because the resolver's ceiling
    (`EXPORT_ROW_CAP`, one repository read) is *lower* than this file's, so it is
    the bound that bites first in practice: without this the file would end at
    the resolver's limit with no marker at all, which is the silent truncation
    this whole module exists to refuse. Marked here rather than at the call site
    so the marker, the `truncated` flag and the response header stay one fact.
    """
    started = time.monotonic()
    generated_at = generated_at or datetime.now(timezone.utc)

    materialised: list[dict[str, Any]] = []
    truncated = False
    reason = ""

    for row in rows:
        if len(materialised) >= row_cap:
            truncated = True
            reason = (
                f"row cap of {row_cap} reached; this file is the first "
                f"{row_cap} rows only. Narrow the date range or add a filter."
            )
            break
        if time.monotonic() - started > time_budget_sec:
            truncated = True
            reason = (
                f"time budget of {time_budget_sec:.0f}s exhausted after "
                f"{len(materialised)} rows. Narrow the date range or add a "
                "filter; the remaining rows were NOT written."
            )
            break
        materialised.append(row)

    if not truncated and source_truncated:
        # The resolver already stopped short. Its bound is reported first only
        # because it is reached first; the file is no less truncated for it.
        truncated = True
        limit_text = (
            f"its {source_row_limit}-row limit"
            if source_row_limit
            else "its row limit"
        )
        reason = (
            f"the view returned only the first {len(materialised)} row(s): the "
            f"query stopped at {limit_text}. More rows exist and were NOT "
            "written. Narrow the date range or add a filter."
        )

    columns = _columns(table_spec, materialised)

    lines: list[str] = [UTF8_BOM]
    lines.extend(
        _header_block(
            view=view,
            module_slug=module_slug,
            table_id=table_id,
            resolved=resolved,
            exported_by=exported_by,
            timezone_name=timezone_name,
            currency=currency,
            availability=availability,
            quality=quality,
            generated_at=generated_at,
        )
    )
    # Blank record between the metadata block and the data, so a spreadsheet
    # user can delete the header rows in one selection.
    lines.append("\r\n")
    lines.append(csv_line([label for _key, label in columns]))

    sanitised = 0
    for row in materialised:
        values = [row.get(key) for key, _label in columns]
        for value in values:
            _text, forced = render_cell(value)
            if forced:
                sanitised += 1
        lines.append(csv_line(values))

    if truncated:
        # Last row, always. A reader who scrolls to the bottom cannot miss it,
        # and a parser can detect it on the marker without parsing the message.
        lines.append(csv_line([TRUNCATION_MARKER, reason]))

    return CsvExport(
        filename=export_filename(module_slug, view.slug, table_id, generated_at),
        lines=lines,
        row_count=len(materialised),
        truncated=truncated,
        truncation_reason=reason,
        sanitised_cells=sanitised,
    )


def export_filename(
    module_slug: str, view_slug: str, table_id: str, generated_at: datetime
) -> str:
    """A filesystem- and header-safe filename.

    Every component is a registry slug, so this is belt-and-braces: a quote or a
    newline reaching `Content-Disposition` is a response-splitting primitive,
    and the cost of filtering to a known-safe alphabet is nothing.
    """
    parts = [module_slug, view_slug, table_id, generated_at.strftime("%Y%m%d-%H%M%S")]
    safe = [
        "".join(ch for ch in str(part) if ch.isalnum() or ch in "-_") or "export"
        for part in parts
    ]
    return "-".join(safe)[:120] + ".csv"


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
def _chunks(export: CsvExport) -> Iterator[bytes]:
    buffer: list[str] = []
    for line in export.lines:
        buffer.append(line)
        if len(buffer) >= STREAM_CHUNK_ROWS:
            yield "".join(buffer).encode("utf-8")
            buffer = []
    if buffer:
        yield "".join(buffer).encode("utf-8")


def csv_streaming_response(export: CsvExport) -> StreamingResponse:
    """Stream a built export.

    `X-Analytics-Row-Count` / `X-Analytics-Truncated` are on the response rather
    than only in the file so an API client learns about truncation without
    parsing to the last row. `Content-Disposition` names a downloaded file;
    `X-Content-Type-Options: nosniff` stops a browser reinterpreting the CSV as
    something it can render inline.
    """
    headers = {
        "Content-Disposition": f'attachment; filename="{export.filename}"',
        "X-Content-Type-Options": "nosniff",
        "X-Analytics-Row-Count": str(export.row_count),
        "X-Analytics-Truncated": "true" if export.truncated else "false",
        "Cache-Control": "no-store",
    }
    if export.truncated:
        headers["X-Analytics-Truncation-Reason"] = _one_line(export.truncation_reason)
    return StreamingResponse(
        _chunks(export),
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )
