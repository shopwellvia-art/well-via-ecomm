"""Generate the frontend's copy of the analytics contract.

The backend owns the 12 modules, the 73 views and the KPI catalogue. React needs
the very same slugs, routes, permissions, availability states and KPI metadata —
and the only way to guarantee the two never drift is to GENERATE the frontend's
copy from the Python definitions instead of hand-maintaining a second list.

This script is read-only with respect to the registry: it imports
``MODULES`` (``app/services/analytics/registry.py``) and ``KPIS``
(``app/services/analytics/kpis.py``) and serialises them into

    frontend/src/features/analytics/registry.contract.json

The output is deterministic on purpose — no timestamp, no hostname, no build id,
keys sorted, fixed indent. Two runs on two machines must produce byte-identical
files, because ``tests/unit/test_analytics_registry.py`` compares the committed
artifact against freshly generated content byte-for-byte. That test is what makes
Python <-> JS drift impossible; a timestamp in here would defeat it.

Usage (from the backend directory, or anywhere — paths are derived from
``__file__``, never from the working directory)::

    python scripts/dump_analytics_registry.py            # write the artifact
    python scripts/dump_analytics_registry.py --check     # CI: fail on drift
    python scripts/dump_analytics_registry.py --stdout    # print, write nothing
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Any

# Make the `app` package importable when run as a plain script.
BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.analytics.kpis import KPIS, kpi_to_dict  # noqa: E402
from app.services.analytics.registry import MODULES  # noqa: E402
from app.services.analytics.types import module_to_dict, view_to_dict  # noqa: E402

#: Bumped only when the *shape* of the artifact changes (not its contents), so
#: the frontend loader can refuse a contract it does not understand.
CONTRACT_VERSION = 1

GENERATOR = "backend/scripts/dump_analytics_registry.py"
REGENERATE_COMMAND = "python backend/scripts/dump_analytics_registry.py"

FRONTEND_DIR = REPO_ROOT / "frontend" / "src" / "features" / "analytics"
CONTRACT_PATH = FRONTEND_DIR / "registry.contract.json"
README_PATH = FRONTEND_DIR / "README.md"

README_TEXT = f"""<!-- GENERATED FILE — DO NOT EDIT BY HAND -->

# Analytics feature — generated contract

`registry.contract.json` in this directory is **generated**. Do not hand-edit it,
and do not restate its contents (slugs, routes, permissions, states, KPI ids)
anywhere else in the frontend — import it.

## Where it comes from

| Artifact | Source of truth |
| --- | --- |
| `registry.contract.json` | `backend/app/services/analytics/registry.py` (modules + views) and `backend/app/services/analytics/kpis.py` (KPI catalogue) |
| This `README.md` | `{GENERATOR}` |

The shared shapes both sides agree on live in
`backend/app/services/analytics/types.py`.

## Regenerating

```bash
{REGENERATE_COMMAND}
```

Run it after ANY change to the registry or the KPI catalogue, and commit the
regenerated JSON in the same commit as the Python change.

## Why it is checked in

CI and `backend/tests/unit/test_analytics_registry.py` compare the committed file
byte-for-byte against freshly generated content:

```bash
python {GENERATOR} --check
```

If that fails, the fix is always to regenerate — never to edit the JSON. The
output is deterministic (sorted keys, fixed indent, no timestamps), so a clean
tree always produces an identical file.

## What is deliberately NOT in here

Presentation. Lucide icon components, chart colours and grid spans cannot be
serialised, so they live in `presentation.js`, keyed by the slugs in this
contract. The contract carries the *meaning*; `presentation.js` carries the look.
"""


def build_contract() -> dict[str, Any]:
    """Assemble the full JSON-native payload from the Python definitions."""
    modules: list[dict[str, Any]] = []
    for module in MODULES:
        data = module_to_dict(module)
        # `module_to_dict` already recurses, but going through `view_to_dict`
        # explicitly keeps the view serialisation in exactly one place.
        data["views"] = [view_to_dict(view) for view in module.views]
        modules.append(data)

    return {
        "version": CONTRACT_VERSION,
        "generated_by": GENERATOR,
        "modules": modules,
        "kpis": [kpi_to_dict(kpi) for kpi in KPIS],
    }


def render_contract() -> str:
    """The exact bytes that belong on disk. Deterministic; no clock, no host."""
    return (
        json.dumps(build_contract(), indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    )


def render_readme() -> str:
    return README_TEXT


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _drift_report(path: Path, expected: str, actual: str | None) -> str:
    """A diff-style explanation plus the one command that fixes it."""
    rel = path.relative_to(REPO_ROOT)
    if actual is None:
        return (
            f"MISSING: {rel} does not exist.\n"
            f"Regenerate it with:\n\n    {REGENERATE_COMMAND}\n"
        )

    diff = list(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{rel} (on disk)",
            tofile=f"{rel} (freshly generated)",
            n=2,
        )
    )
    shown = diff[:80]
    truncated = "" if len(diff) == len(shown) else f"\n... ({len(diff) - len(shown)} more diff lines)\n"
    return (
        f"DRIFT: {rel} does not match the Python registry.\n"
        f"{''.join(shown)}{truncated}\n"
        "The JSON is generated — never edit it by hand. Regenerate with:\n\n"
        f"    {REGENERATE_COMMAND}\n"
    )


def check() -> int:
    """Exit status 0 when both artifacts are up to date, 1 otherwise."""
    problems: list[str] = []
    for path, expected in ((CONTRACT_PATH, render_contract()), (README_PATH, render_readme())):
        actual = _read(path)
        if actual != expected:
            problems.append(_drift_report(path, expected, actual))

    if problems:
        sys.stderr.write("\n".join(problems))
        return 1

    print(f"OK: {CONTRACT_PATH.relative_to(REPO_ROOT)} is up to date.")
    return 0


def write() -> int:
    FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    contract = render_contract()
    CONTRACT_PATH.write_text(contract, encoding="utf-8")
    README_PATH.write_text(render_readme(), encoding="utf-8")

    view_count = sum(len(module.views) for module in MODULES)
    print(
        f"Wrote {CONTRACT_PATH.relative_to(REPO_ROOT)} "
        f"({len(MODULES)} modules, {view_count} views, {len(KPIS)} KPIs, "
        f"{len(contract)} bytes)"
    )
    print(f"Wrote {README_PATH.relative_to(REPO_ROOT)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="Do not write. Exit 1 if the committed artifact differs from freshly "
        "generated content (use this in CI).",
    )
    group.add_argument(
        "--stdout",
        action="store_true",
        help="Print the contract JSON instead of writing any file.",
    )
    args = parser.parse_args(argv)

    if args.stdout:
        sys.stdout.write(render_contract())
        return 0
    if args.check:
        return check()
    return write()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
