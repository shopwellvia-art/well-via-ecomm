<!-- GENERATED FILE — DO NOT EDIT BY HAND -->

# Analytics feature — generated contract

`registry.contract.json` in this directory is **generated**. Do not hand-edit it,
and do not restate its contents (slugs, routes, permissions, states, KPI ids)
anywhere else in the frontend — import it.

## Where it comes from

| Artifact | Source of truth |
| --- | --- |
| `registry.contract.json` | `backend/app/services/analytics/registry.py` (modules + views) and `backend/app/services/analytics/kpis.py` (KPI catalogue) |
| This `README.md` | `backend/scripts/dump_analytics_registry.py` |

The shared shapes both sides agree on live in
`backend/app/services/analytics/types.py`.

## Regenerating

```bash
python backend/scripts/dump_analytics_registry.py
```

Run it after ANY change to the registry or the KPI catalogue, and commit the
regenerated JSON in the same commit as the Python change.

## Why it is checked in

CI and `backend/tests/unit/test_analytics_registry.py` compare the committed file
byte-for-byte against freshly generated content:

```bash
python backend/scripts/dump_analytics_registry.py --check
```

If that fails, the fix is always to regenerate — never to edit the JSON. The
output is deterministic (sorted keys, fixed indent, no timestamps), so a clean
tree always produces an identical file.

## What is deliberately NOT in here

Presentation. Lucide icon components, chart colours and grid spans cannot be
serialised, so they live in `presentation.js`, keyed by the slugs in this
contract. The contract carries the *meaning*; `presentation.js` carries the look.
