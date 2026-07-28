"""CSP violation collector for the Report-Only rollout.

Browsers POST here when the candidate Content-Security-Policy in
`frontend/nginx.conf` *would* have blocked something. Nothing is blocked while
the policy is Report-Only — these reports are the evidence used to decide which
hosts genuinely belong in the enforced policy, instead of guessing at a list of
Google and Microsoft domains and finding out in production.

Three properties this endpoint needs and most naive implementations lack:

**It must be anonymous.** The browser posts it with no credentials, and the
violation that matters most is on a page a logged-out customer is looking at.

**It must be cheap to abuse.** An unauthenticated POST endpoint is a free
write primitive, so it is rate-limited, size-capped, and stores a bounded
aggregate rather than one row per report. A misconfigured tag on a busy page can
emit thousands of identical reports per minute.

**It must not become a PII sink.** `blocked-uri` and `document-uri` are URLs
from a customer's session and can carry a search term, an email in a query
string, or a token. They are truncated and query strings are stripped before
storage — the host and directive are what the decision needs; the full URL is
not worth the liability.
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import rate_limit_by_ip

router = APIRouter()
log = logging.getLogger("analytics.csp")

#: Reports larger than this are truncated before parsing. Chrome's reports are
#: well under 2 KB; anything far larger is malformed or hostile.
MAX_BODY_BYTES = 8192

#: Stored URL length. Enough to identify a host and path, short enough that a
#: token or an email in a query string cannot ride along in full.
MAX_URI_CHARS = 200


def _safe_uri(raw: str | None) -> str:
    """Host + path only, truncated. Query and fragment are DISCARDED.

    A CSP report's `document-uri` is whatever page the customer was on, and its
    query string routinely carries a search term. Deciding whether
    `www.googletagmanager.com` belongs in `script-src` needs the host, never the
    query — so the part that could contain someone's email is dropped before it
    is ever written down.
    """
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        cleaned = f"{parts.scheme}://{parts.netloc}{parts.path}" if parts.netloc else parts.path
    except ValueError:
        cleaned = raw
    return cleaned[:MAX_URI_CHARS]


@router.post(
    "/csp-report",
    status_code=status.HTTP_204_NO_CONTENT,
    include_in_schema=False,
    dependencies=[
        Depends(rate_limit_by_ip(scope="csp_report", limit=60, window_sec=60)),
    ],
)
async def collect_csp_report(request: Request) -> Response:
    """Record one CSP violation report. Always answers 204.

    Never returns an error, whatever arrives. A browser cannot act on a 4xx here
    and would keep retrying; worse, a failing report endpoint on a page that is
    otherwise fine turns a diagnostic into a visible console error that looks
    like a site fault to anyone with devtools open.
    """
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        body = body[:MAX_BODY_BYTES]

    try:
        payload = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("csp-report: unparseable body (%d bytes)", len(body))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Type-check BEFORE reaching for a key. A JSON array parses fine and then
    # raises on `.get`, which would 500 an endpoint whose whole contract is that
    # it never fails — and this is an anonymous endpoint, so the shape is
    # entirely attacker-controlled. Browsers send the wrapped form; the
    # newer Reporting API sends the bare object, so both are accepted.
    if not isinstance(payload, dict):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    report = payload.get("csp-report") or payload
    if not isinstance(report, dict):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Logged as structured fields rather than written to a table: the rollout
    # needs an aggregate ("which hosts were blocked, on which directive"), which
    # a log query answers, and this way a flood costs log volume rather than
    # unbounded rows in the operational database.
    log.warning(
        "csp-violation directive=%s blocked=%s document=%s disposition=%s",
        str(report.get("effective-directive") or report.get("violated-directive"))[:64],
        _safe_uri(report.get("blocked-uri")),
        _safe_uri(report.get("document-uri")),
        str(report.get("disposition") or "report")[:16],
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
