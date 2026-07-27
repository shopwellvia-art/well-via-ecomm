/**
 * Vitest setup (node environment).
 *
 * The auth + guest-cart stores use zustand `persist`, which reads
 * localStorage synchronously when the store module is imported. Plain node
 * has no Web Storage, so install a minimal in-memory implementation before
 * any test module loads.
 */
class MemoryStorage {
  #map = new Map();

  getItem(key) {
    return this.#map.has(key) ? this.#map.get(key) : null;
  }

  setItem(key, value) {
    this.#map.set(String(key), String(value));
  }

  removeItem(key) {
    this.#map.delete(key);
  }

  clear() {
    this.#map.clear();
  }

  key(index) {
    return [...this.#map.keys()][index] ?? null;
  }

  get length() {
    return this.#map.size;
  }
}

// Override unconditionally: newer node versions expose a native localStorage
// that throws unless started with --localstorage-file, which makes zustand's
// persist middleware spam "storage is currently unavailable" warnings.
for (const name of ['localStorage', 'sessionStorage']) {
  Object.defineProperty(globalThis, name, {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
}
