/**
 * The backend's copy of the event contract, read at test time.
 *
 * The frontend constants in `dataLayer.js` are a hand-written mirror of
 * `backend/app/services/analytics/tracking_events.py`. A mirror is only useful
 * if something notices when it stops matching, so this module goes and reads
 * the other side.
 *
 * Two sources, in order of preference:
 *
 *   1. **A dumped JSON contract**, if the backend has produced one — the same
 *      arrangement `registry.contract.json` uses. `to_json_dict()` in the
 *      Python module exists precisely to be dumped this way. The moment that
 *      file lands at one of the candidate paths, these tests start comparing
 *      against it with no change here.
 *   2. **The Python source**, parsed. Coarse, but it reads the definitions that
 *      actually run, and it is available today without waiting for the dump.
 *
 * Both are best-effort: `null` from either just means the corresponding test
 * falls back to the transcribed expectations in `contract.test.js`, which are
 * asserted unconditionally either way.
 */
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const DUMP_CANDIDATES = [
  '../tracking.contract.json',
  '../../analytics/tracking.contract.json',
  '../../../../../backend/app/services/analytics/tracking_events.contract.json',
];

const PYTHON_SOURCE = '../../../../../backend/app/services/analytics/tracking_events.py';

function resolve(relative) {
  return fileURLToPath(new URL(relative, import.meta.url));
}

function readIfPresent(relative) {
  const path = resolve(relative);
  return existsSync(path) ? readFileSync(path, 'utf8') : null;
}

/** The dumped JSON contract, or `null` if no peer has produced one yet. */
export function dumpedContract() {
  for (const candidate of DUMP_CANDIDATES) {
    const raw = readIfPresent(candidate);
    if (raw) {
      try {
        return { path: candidate, contract: JSON.parse(raw) };
      } catch {
        return null; // a corrupt dump is not a contract
      }
    }
  }
  return null;
}

function quoted(text) {
  return [...text.matchAll(/"([^"]+)"/g)].map((match) => match[1]);
}

function tupleLiteral(source, name) {
  const match = source.match(new RegExp(`\\n${name}[^=]*= \\(([\\s\\S]*?)\\n\\)`));
  return match ? quoted(match[1]) : null;
}

function classConstants(source, className) {
  const body = source.match(
    new RegExp(`class ${className}:[\\s\\S]*?(?=\\n\\nclass |\\n\\n#|\\n\\n[A-Z_]+ =)`)
  );
  if (!body) return null;
  const pairs = [...body[0].matchAll(/^ {4}([A-Z_0-9]+) = "([a-z_0-9]+)"$/gm)];
  return Object.fromEntries(pairs.map((match) => [match[1], match[2]]));
}

/**
 * The Python contract, parsed out of the source. `null` when the backend is not
 * checked out alongside the frontend (a frontend-only CI job, for instance).
 */
export function pythonContract() {
  const source = readIfPresent(PYTHON_SOURCE);
  if (!source) return null;

  const ev = classConstants(source, 'Ev');
  const bizEv = classConstants(source, 'BizEv');
  if (!ev || !bizEv) return null;

  const requiredBlock = source.match(/REQUIRED_PARAMS[^=]*= \{([\s\S]*?)\n\}/);
  const requiredParams = {};
  if (requiredBlock) {
    for (const match of requiredBlock[1].matchAll(/Ev\.([A-Z_0-9]+): \(([^)]*)\)/g)) {
      requiredParams[ev[match[1]]] = quoted(match[2]);
    }
  }

  const allowed = source.match(/PII_ALLOWED_EXACT = frozenset\(\s*\{([\s\S]*?)\n {4}\}/);
  const schemaVersion = source.match(/^SCHEMA_VERSION = (\d+)$/m);

  return {
    schema_version: schemaVersion ? Number(schemaVersion[1]) : null,
    events: Object.values(ev).sort(),
    business_events: Object.values(bizEv).sort(),
    item_fields: tupleLiteral(source, 'ITEM_FIELDS'),
    required_params: requiredParams,
    pii_denylist: tupleLiteral(source, 'PII_DENYLIST'),
    pii_allowed_exact: allowed ? quoted(allowed[1]).sort() : null,
    // The `ORD{id}` fallback is the half of the transaction-id rule a constant
    // cannot express, so it is checked as source text.
    transaction_id_fallback: source.includes('f"ORD{order_id}"'),
  };
}
