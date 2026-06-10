import { Home, Briefcase, MapPin, Star, Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { cn } from '@/lib/utils.js';

const LABEL_META = {
  HOME:  { icon: Home,      tone: 'accent',   iconBg: 'bg-accent/12 text-accent'          },
  WORK:  { icon: Briefcase, tone: 'info',      iconBg: 'bg-info/12 text-info'              },
  OTHER: { icon: MapPin,    tone: 'neutral',   iconBg: 'bg-fill-strong text-ink-secondary' },
};

/**
 * Formatted address block with badges and action buttons.
 *
 * Props
 *   address      – AddressRead object
 *   onEdit       – called when Edit is clicked (omit to hide)
 *   onDelete     – called when Delete is clicked (omit to hide)
 *   onSetDefault – called when "Set default" is clicked (omit to hide)
 *   compact      – smaller visual variant for picker use
 *   selected     – highlight ring (picker mode)
 *   deleting     – shows spinner on delete button
 */
export default function AddressCard({
  address,
  onEdit,
  onDelete,
  onSetDefault,
  compact = false,
  selected = false,
  deleting = false,
}) {
  const meta = LABEL_META[address.label] || LABEL_META.OTHER;
  const Icon = meta.icon;
  const labelText = address.label.charAt(0) + address.label.slice(1).toLowerCase();

  return (
    <div
      className={cn(
        'rounded-lg border transition-all duration-200',
        selected
          ? 'border-accent bg-accent/12 shadow-glow-sm'
          : 'border-line-subtle bg-bg-elevated',
        compact ? 'p-3' : 'p-4',
      )}
    >
      {/* Top row: label icon + badges */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Icon chip */}
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide',
            meta.iconBg,
          )}
        >
          <Icon className="size-3" aria-hidden="true" />
          {labelText}
        </span>

        {/* Default badge */}
        {address.is_default && (
          <Badge tone="accent" dot size="sm">
            Default
          </Badge>
        )}
      </div>

      {/* Address body */}
      <p className={cn('mt-2.5 font-semibold text-ink-primary', compact ? 'text-xs' : 'text-sm')}>
        {address.full_name}
      </p>
      <p className={cn('mt-0.5 text-ink-secondary', compact ? 'text-xs' : 'text-sm')}>
        {address.line1}
        {address.line2 ? `, ${address.line2}` : ''}
        {address.landmark ? ` (${address.landmark})` : ''}
      </p>
      <p className={cn('text-ink-secondary', compact ? 'text-xs' : 'text-sm')}>
        {address.city}, {address.state}{' '}
        <span className="nums">– {address.pincode}</span>
      </p>
      <p
        className={cn(
          'mt-0.5 nums text-ink-tertiary',
          compact ? 'text-[11px]' : 'text-xs',
        )}
      >
        {address.phone}
      </p>

      {/* Action buttons */}
      {(onEdit || onDelete || onSetDefault) && (
        <div className="mt-3 flex flex-wrap items-center gap-1 border-t border-line-subtle pt-3">
          {onEdit && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              iconOnly
              aria-label={`Edit address for ${address.full_name}`}
              onClick={() => onEdit(address)}
              className="focus-visible:focus-ring"
            >
              <Pencil className="size-3.5" aria-hidden="true" />
            </Button>
          )}
          {!address.is_default && onSetDefault && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onSetDefault(address.id)}
              className="text-ink-secondary hover:text-accent"
            >
              <Star className="size-3.5" aria-hidden="true" />
              Set default
            </Button>
          )}
          {onDelete && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              iconOnly
              aria-label={`Delete address for ${address.full_name}`}
              onClick={() => onDelete(address.id)}
              loading={deleting}
              className="ml-auto text-ink-tertiary hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
