import { useEffect } from 'react';

/**
 * Keep Tab inside a container while it is open, and give focus back when it
 * closes.
 *
 * Focus lands on the **container**, not on a button. Focusing "Accept" would
 * make Return-on-page-load grant consent, which is a decision the customer's
 * keyboard made for them; focusing "Reject" has the mirror-image problem. The
 * container is `tabIndex={-1}`, so it takes focus without becoming a tab stop.
 *
 * A trap with no way out is a WCAG 2.1.2 keyboard trap, so the caller must
 * always render a dismissal path inside the container — here that is the two
 * equally-weighted decision buttons plus Escape.
 */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

export function useFocusTrap(ref, active) {
  useEffect(() => {
    const node = ref.current;
    if (!active || !node || typeof document === 'undefined') return undefined;

    const previouslyFocused = document.activeElement;
    node.focus({ preventScroll: true });

    function onKeyDown(event) {
      if (event.key !== 'Tab') return;
      const items = [...node.querySelectorAll(FOCUSABLE)].filter(
        (el) => el.getAttribute('aria-hidden') !== 'true'
      );
      if (items.length === 0) {
        event.preventDefault();
        node.focus({ preventScroll: true });
        return;
      }

      const first = items[0];
      const last = items[items.length - 1];
      const current = document.activeElement;

      // Focus outside the container (the page behind, or the container itself)
      // re-enters at whichever end the customer is heading for.
      if (!node.contains(current) || current === node) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
        return;
      }
      if (event.shiftKey && current === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && current === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [ref, active]);
}
