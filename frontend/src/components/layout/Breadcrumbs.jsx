import { Link } from 'react-router-dom';
import { ChevronRight, Home } from 'lucide-react';
import { cn } from '@/lib/utils.js';

/**
 * Frosted pill breadcrumb used across the storefront.
 *
 * Usage:
 *   <Breadcrumbs items={[{ label: 'Shop', to: '/products' }]} current="Checkout" />
 *
 * `Home` is prepended automatically (pass includeHome={false} to opt out).
 * `items` are the intermediate links; `current` is the active page, rendered
 * in the primary ink and non-interactive. Flat plain-text marketplace style.
 */
export function Breadcrumbs({ items = [], current, className, includeHome = true }) {
  const links = [
    ...(includeHome ? [{ label: 'Home', to: '/', icon: Home }] : []),
    ...items,
  ];

  return (
    <nav aria-label="Breadcrumb" className={className}>
      <ol className="inline-flex max-w-full flex-wrap items-center gap-x-1 gap-y-1 text-xs">
        {links.map(({ label, to, icon: Icon }) => (
          <li key={`${to}-${label}`} className="inline-flex items-center gap-1">
            <Link
              to={to}
              className="inline-flex items-center gap-1 rounded-sm font-medium text-ink-secondary transition-colors hover:text-accent focus-visible:focus-ring"
            >
              {Icon && <Icon className="size-3.5" aria-hidden="true" />}
              {label}
            </Link>
            <ChevronRight className="size-3.5 text-ink-tertiary/70" aria-hidden="true" />
          </li>
        ))}
        {current && (
          <li className="inline-flex min-w-0">
            <span
              aria-current="page"
              className={cn('max-w-[45vw] truncate font-semibold text-ink-primary sm:max-w-xs')}
            >
              {current}
            </span>
          </li>
        )}
      </ol>
    </nav>
  );
}
