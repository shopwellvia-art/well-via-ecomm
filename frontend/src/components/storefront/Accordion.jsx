import { useId, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Accordion — accessible disclosure row for the storefront.
 *
 * A full-width header button (aria-expanded + aria-controls) toggles a
 * region panel; the chevron rotates on open and the panel is removed from
 * the accessibility tree via `hidden` when closed.
 *
 * Props:
 *   title       — header label (required)
 *   icon        — optional lucide icon component rendered before the title
 *   defaultOpen — start expanded (default false)
 *   className   — extra classes for the outer wrapper
 */
export default function Accordion({ title, icon: Icon, defaultOpen = false, className, children }) {
  const [open, setOpen] = useState(defaultOpen);
  const buttonId = useId();
  const panelId = useId();

  return (
    <div className={cn('overflow-hidden rounded-xl border border-wline bg-wcard', className)}>
      <button
        type="button"
        id={buttonId}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex w-full items-center gap-3 px-4 py-3.5 text-left',
          'transition-colors hover:bg-wpaper/60 focus-visible:outline-none',
        )}
      >
        {Icon && (
          <span
            className="grid size-8 shrink-0 place-items-center rounded-lg bg-wpaper text-wgreen"
            aria-hidden="true"
          >
            <Icon className="size-4" />
          </span>
        )}
        <span className="flex-1 text-sm font-medium text-wink">{title}</span>
        <ChevronDown
          className={cn(
            'size-4 shrink-0 text-wmuted transition-transform duration-200',
            open && 'rotate-180',
          )}
          aria-hidden="true"
        />
      </button>

      <div
        id={panelId}
        role="region"
        aria-labelledby={buttonId}
        hidden={!open}
        className="border-t border-wline px-4 py-4 text-sm leading-relaxed text-wmuted"
      >
        {children}
      </div>
    </div>
  );
}
