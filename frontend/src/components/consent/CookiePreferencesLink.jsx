import { cn } from '@/lib/utils.js';

import { openConsentPreferences } from './preferencesBus.js';

/**
 * The way back in.
 *
 * A consent record with no route back to it is a record the customer can never
 * change, and a decision that cannot be withdrawn as easily as it was given is
 * not a decision the GDPR recognises. This link is the whole withdrawal path:
 * it is on every storefront page, it is a real button (keyboard reachable, with
 * the standard focus ring), and it opens the dialog straight on the switches.
 *
 * Styling is passed in, because it lives inside the footer's own palette rather
 * than the app's surface tokens.
 */
export default function CookiePreferencesLink({ className }) {
  return (
    <button
      type="button"
      onClick={openConsentPreferences}
      className={cn(
        'cursor-pointer border-0 bg-transparent p-0 underline underline-offset-4 focus-visible:focus-ring',
        className,
      )}
    >
      Cookie preferences
    </button>
  );
}
