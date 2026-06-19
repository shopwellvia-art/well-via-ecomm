"""SQL normalization + table extraction for slow-query aggregation.

Regex-based and dependency-free. Two goals:

1. **Privacy** — never persist raw literal values. SQLAlchemy + PyMySQL already
   pass bound parameters separately (the statement carries ``%s`` placeholders,
   not the values), but we additionally strip any inlined string/number literals
   so nothing sensitive can leak into the telemetry store.
2. **Aggregation** — collapse a family of queries that differ only in their
   literals/placeholders to one stable shape, so the dashboard can group by a
   fingerprint and roll up by table.
"""
from __future__ import annotations

import hashlib
import re

# 'string literals' (handles '' escaping). Replaced before numbers so digits
# inside strings are already gone.
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
# pyformat / format placeholders produced by PyMySQL: %s and %(name)s
_PARAM_RE = re.compile(r"%\((?:[^)]*)\)s|%s")
# Standalone numeric literals (int/float). The \b guards stop us mangling
# identifiers like `aaa_p1_aaa` where the digit is surrounded by word chars.
_NUMBER_RE = re.compile(r"\b\d+\.?\d*\b")
# Collapse  IN (?, ?, ?)  ->  IN (?)
_IN_LIST_RE = re.compile(r"\bIN\s*\(\s*(?:\?\s*,\s*)*\?\s*\)", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
# First table after FROM / INTO / UPDATE / JOIN; optional backtick quoting.
_TABLE_RE = re.compile(
    r"\b(?:from|into|update|join)\s+`?([A-Za-z0-9_$.]+)`?",
    re.IGNORECASE,
)
_OPERATIONS = {"SELECT", "INSERT", "UPDATE", "DELETE", "REPLACE"}


def normalize_sql(statement: str) -> tuple[str, str, str | None]:
    """Return ``(normalized_sql, fingerprint_hash, operation)``.

    ``normalized_sql`` has all literals/placeholders replaced with ``?``,
    IN-lists collapsed, and whitespace squeezed. ``fingerprint_hash`` is the
    md5 hex of the normalized SQL (stable across literal-only differences).
    """
    sql = statement or ""
    sql = _STRING_RE.sub("?", sql)
    sql = _PARAM_RE.sub("?", sql)
    sql = _NUMBER_RE.sub("?", sql)
    sql = _WS_RE.sub(" ", sql).strip()
    sql = _IN_LIST_RE.sub("IN (?)", sql)

    fingerprint = hashlib.md5(sql.encode("utf-8", "ignore")).hexdigest()

    operation: str | None = None
    head = sql[:12].lstrip().split(" ", 1)[0].upper() if sql else ""
    if head in _OPERATIONS:
        operation = head

    return sql, fingerprint, operation


def extract_table(statement: str) -> str | None:
    """Best-effort primary table name from a SQL statement, or None."""
    if not statement:
        return None
    match = _TABLE_RE.search(statement)
    if not match:
        return None
    # Strip schema qualifier (`db.table` -> `table`) for cleaner grouping.
    return match.group(1).split(".")[-1][:128]
