import { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { Badge } from '@/components/ui/Badge.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { cn } from '@/lib/utils.js';

import { useFocusTrap } from './useFocusTrap.js';
import {
  CONSENT_UI_CATEGORIES,
  acceptAllPatch,
  draftFromConsent,
  patchFromDraft,
  rejectAllPatch,
  setDraftCategory,
} from './visibility.js';

/**
 * The consent dialog.
 *
 * **Reject and Accept are the same control twice.** Same `<Button>` variant,
 * same size, same width, same row — the only difference between them is the
 * word on the face. That is not decoration: a banner where "Accept" is a filled
 * button and "Reject" is grey text collects clicks, not agreement, and a
 * regulator reading the two together sees a choice that was never offered.
 * If you are about to restyle one of them, restyle both.
 *
 * "Manage preferences" is deliberately quieter, because it is not a third
 * answer to the question — it is a way to see the question in more detail.
 *
 * Motion is CSS (`animate-rise` / `animate-dim`), which the global
 * `prefers-reduced-motion` block already reduces to nothing. Framer Motion runs
 * in JS and would ignore that block, so it is not used here.
 */

/** One switchable category. `necessary` renders through `LockedRow` instead. */
function CategoryRow({ category, checked, onChange }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-line-subtle py-4 first:border-t-0 first:pt-0">
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink-primary">{category.label}</p>
        <p className="mt-1 text-xs leading-relaxed text-ink-secondary">{category.description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={category.label}
        onClick={() => onChange(!checked)}
        className={cn(
          'relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors focus-visible:focus-ring',
          checked ? 'bg-accent' : 'bg-fill-strong',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'block size-5 rounded-full bg-bg-elevated shadow-sm transition-transform',
            checked ? 'translate-x-[22px]' : 'translate-x-0.5',
          )}
        />
      </button>
    </div>
  );
}

/** `necessary`: a badge, not a disabled switch — a dead control is a lie. */
function LockedRow({ category }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-line-subtle py-4 first:border-t-0 first:pt-0">
      <div className="min-w-0">
        <p className="text-sm font-medium text-ink-primary">{category.label}</p>
        <p className="mt-1 text-xs leading-relaxed text-ink-secondary">{category.description}</p>
      </div>
      <Badge tone="neutral" className="mt-0.5 shrink-0">
        Always on
      </Badge>
    </div>
  );
}

export default function ConsentBanner({
  consent,
  initialView = 'summary',
  onDecide,
  onDismiss,
}) {
  const dialogRef = useRef(null);
  const uid = useId();
  const titleId = `${uid}-title`;
  const bodyId = `${uid}-body`;

  const [view, setView] = useState(initialView);
  const [draft, setDraft] = useState(() => draftFromConsent(consent));

  // Re-opened from the footer: show the switches as they were last saved, not
  // as they were when this component first mounted.
  useEffect(() => {
    setView(initialView);
    setDraft(draftFromConsent(consent));
  }, [initialView, consent]);

  useFocusTrap(dialogRef, true);

  function onKeyDown(event) {
    if (event.key !== 'Escape') return;
    event.stopPropagation();
    // Escape backs out of the detail view, and out of the dialog itself —
    // without recording anything. Nothing is granted by walking away, and
    // because no decision was written down the question comes back next visit
    // rather than being silently answered "no" on the customer's behalf.
    if (view === 'preferences' && initialView !== 'preferences') setView('summary');
    else onDismiss();
  }

  return (
    <div
      className="fixed inset-0 z-[150] flex items-end justify-center sm:items-center sm:p-4"
      onKeyDown={onKeyDown}
    >
      {/*
       * Visual dim only. Clicking it does nothing: a click on the page behind
       * is not an answer, and treating it as "accept" (or as "reject") invents
       * a decision. The way out is a button, or Escape.
       */}
      <div aria-hidden="true" className="absolute inset-0 bg-black/50 animate-dim" />

      {/* The dialog semantics sit on the wrapper, not on <Card>: Card is a plain
          function component, so a ref handed to it would be dropped and the
          focus trap would have nothing to trap. */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-live="polite"
        aria-labelledby={titleId}
        aria-describedby={bodyId}
        tabIndex={-1}
        className="relative w-full max-w-2xl outline-none"
      >
        <Card className="animate-rise max-h-[88vh] overflow-y-auto rounded-b-none p-5 shadow-lg sm:rounded-sm sm:p-6">
          <h2 id={titleId} className="text-h3 text-ink-primary">
            {view === 'summary' ? 'Your privacy choices' : 'Manage your privacy choices'}
          </h2>

          {view === 'summary' ? (
            <>
              <p id={bodyId} className="mt-2 text-sm leading-relaxed text-ink-secondary">
                We need a few cookies to keep you signed in and to hold your cart — those
                stay on. Everything else is off unless you turn it on: analytics and session
                recording, advertising measurement, and personalized recommendations. You can
                change your mind at any time from{' '}
                <span className="text-ink-primary">Cookie preferences</span> in the footer.
              </p>

              {/*
               * Equal weight, enforced by construction: one grid, two identical
               * <Button variant="primary" block> children. Neither is a link,
               * neither is grey, neither is behind a second click.
               */}
              <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Button variant="primary" size="md" block onClick={() => onDecide(rejectAllPatch())}>
                  Reject all
                </Button>
                <Button variant="primary" size="md" block onClick={() => onDecide(acceptAllPatch())}>
                  Accept all
                </Button>
              </div>

              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <Button variant="ghost" size="sm" onClick={() => setView('preferences')}>
                  Manage preferences
                </Button>
                <Link
                  to="/privacy"
                  className="rounded-xs px-1 text-xs text-ink-tertiary underline underline-offset-4 hover:text-ink-primary focus-visible:focus-ring"
                >
                  Privacy policy
                </Link>
              </div>
            </>
          ) : (
            <>
              <p id={bodyId} className="mt-2 text-sm leading-relaxed text-ink-secondary">
                Every switch starts off. Turn on only what you are happy for us to do, then
                save — and come back here through <span className="text-ink-primary">Cookie
                preferences</span> in the footer whenever you want to change or withdraw it.
              </p>

              <div className="mt-5">
                {CONSENT_UI_CATEGORIES.map((category) =>
                  category.locked ? (
                    <LockedRow key={category.id} category={category} />
                  ) : (
                    <CategoryRow
                      key={category.id}
                      category={category}
                      checked={draft[category.id] === true}
                      onChange={(value) =>
                        setDraft((current) => setDraftCategory(current, category.id, value))
                      }
                    />
                  ),
                )}
              </div>

              <div className="mt-5 flex flex-col gap-3">
                <Button
                  variant="primary"
                  size="md"
                  block
                  onClick={() => onDecide(patchFromDraft(draft))}
                >
                  Save my choices
                </Button>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <Button variant="ghost" size="sm" onClick={() => setView('summary')}>
                    Back
                  </Button>
                  <Link
                    to="/privacy"
                    className="rounded-xs px-1 text-xs text-ink-tertiary underline underline-offset-4 hover:text-ink-primary focus-visible:focus-ring"
                  >
                    Privacy policy
                  </Link>
                </div>
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}
