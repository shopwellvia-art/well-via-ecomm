/**
 * A DOM small enough to assert against.
 *
 * The suite runs in plain node (see `vitest.config.js`), so there is no
 * `window` and no `document`. The tracking modules are written to work that
 * way — every access is guarded — but the interesting questions ("did it
 * inject a script?", "did it inject a *second* one?") need something to inject
 * into. This is that something: a head that collects appended nodes, and
 * nothing else. jsdom would answer the same questions at fifty times the cost
 * and would let a test accidentally depend on real DOM behaviour the modules
 * never rely on.
 */

function makeElement(tagName) {
  return {
    tagName: String(tagName).toUpperCase(),
    attributes: {},
    setAttribute(key, value) {
      this.attributes[key] = String(value);
    },
    getAttribute(key) {
      return Object.prototype.hasOwnProperty.call(this.attributes, key)
        ? this.attributes[key]
        : null;
    },
  };
}

/**
 * Install `globalThis.window` / `globalThis.document`.
 * Returns the handles a test needs, including the live list of appended nodes.
 */
export function installFakeDom({ pathname = '/', cookie = '', innerWidth = 1280 } = {}) {
  const appended = [];
  const maskable = [];

  const head = {
    appendChild(node) {
      appended.push(node);
      return node;
    },
  };

  const fakeDocument = {
    cookie,
    head,
    createElement: makeElement,
    getElementById(id) {
      return appended.find((node) => node.id === id) || null;
    },
    querySelectorAll() {
      return maskable;
    },
  };

  const fakeWindow = {
    innerWidth,
    location: { pathname, href: `https://shopwellvia.in${pathname}` },
    document: fakeDocument,
  };

  globalThis.window = fakeWindow;
  globalThis.document = fakeDocument;

  return { window: fakeWindow, document: fakeDocument, appended, maskable };
}

export function uninstallFakeDom() {
  delete globalThis.window;
  delete globalThis.document;
}

/** Everything currently in the data layer. `[]` when there is no window. */
export function dataLayerEntries() {
  const dl = globalThis.window && globalThis.window.dataLayer;
  return Array.isArray(dl) ? dl : [];
}

/** Data-layer entries for one contract event name. */
export function eventsNamed(name) {
  return dataLayerEntries().filter((entry) => entry && entry.event === name);
}
