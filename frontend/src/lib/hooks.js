import { useEffect, useState } from 'react';

/**
 * Trail `value` by `ms`, resetting the timer on every change.
 *
 * Use for anything that turns keystrokes into requests: the returned value only
 * settles once typing pauses, so a 12-character query costs one call instead of
 * twelve.
 *
 * Five admin pages (Orders, Reviews, Customers, Loyalty, Team) each declare a
 * private copy of this hook. They predate this module and are left alone on
 * purpose — nothing about them is broken. New code should import from here.
 */
export function useDebounced(value, ms = 250) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}
