import { useEffect, useState } from 'react';
import { useLocation } from 'react-router-dom';

import { getConsent, onConsentChange, setConsent } from '@/features/tracking/consent.js';

import ConsentBanner from './ConsentBanner.jsx';
import { onOpenConsentPreferences } from './preferencesBus.js';
import { shouldShowBanner } from './visibility.js';

/**
 * Decides whether the consent dialog is on screen, and writes the answer down.
 *
 * Mounted once at the app root, inside the router. It holds three pieces of
 * state and nothing else:
 *
 *   consent    — mirrored from the store, so a change anywhere re-renders here
 *   reopened   — the footer link asked for the dialog back
 *   dismissed  — Escape was pressed; hidden for this page session only, and
 *                nothing was recorded, so the question survives to next visit
 *
 * All four visibility rules live in `shouldShowBanner`, which is pure and
 * tested. This component contributes no rule of its own.
 */
export default function ConsentGate() {
  const { pathname } = useLocation();
  const [consent, setConsentState] = useState(getConsent);
  const [reopened, setReopened] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => onConsentChange(setConsentState), []);
  useEffect(() => onOpenConsentPreferences(() => setReopened(true)), []);

  const open = shouldShowBanner({
    pathname,
    consent,
    reopened,
    dismissedForSession: dismissed,
  });
  if (!open) return null;

  return (
    <ConsentBanner
      // A remount per view keeps the switches in step with a decision that was
      // recorded elsewhere (a second tab, say) rather than showing stale ones.
      key={reopened ? 'reopened' : 'first-run'}
      consent={consent}
      initialView={reopened ? 'preferences' : 'summary'}
      onDecide={(patch) => {
        setConsent(patch);
        setReopened(false);
        setDismissed(false);
      }}
      onDismiss={() => {
        setReopened(false);
        setDismissed(true);
      }}
    />
  );
}
