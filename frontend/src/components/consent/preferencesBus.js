/**
 * A two-function bus so anything on the page can re-open the consent dialog.
 *
 * The footer link and the banner sit in different subtrees — the banner is
 * mounted once at the app root, the link is inside the storefront layout — and
 * a context provider wrapping the whole app to carry a single "open" signal is
 * more machinery than the signal is worth.
 */

const listeners = new Set();

/** Re-open the dialog on the preferences view. Called by the footer link. */
export function openConsentPreferences() {
  for (const listener of [...listeners]) {
    try {
      listener();
    } catch {
      // one bad subscriber must not stop the others being told
    }
  }
}

/** Subscribe. Returns the unsubscribe function. */
export function onOpenConsentPreferences(callback) {
  listeners.add(callback);
  return () => listeners.delete(callback);
}
