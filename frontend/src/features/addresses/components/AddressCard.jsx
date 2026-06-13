import { Home, Briefcase, MapPin, MapPinned, Star, Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { cn } from '@/lib/utils.js';

// Keys match the API's lowercase AddressLabel values ("home"/"work"/"other").
const LABEL_META = {
  home:  { icon: Home,      tone: 'accent',   iconBg: 'bg-accent/12 text-accent'          },
  work:  { icon: Briefcase, tone: 'info',      iconBg: 'bg-info/12 text-info'              },
  other: { icon: MapPin,    tone: 'neutral',   iconBg: 'bg-fill-strong text-ink-secondary' },
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
  const labelKey = String(address.label || 'other').toLowerCase();
  const meta = LABEL_META[labelKey] || LABEL_META.other;
  const Icon = meta.icon;
  const labelText = labelKey.charAt(0).toUpperCase() + labelKey.slice(1);

  return (
    <div
      className={cn(
        'rounded-sm border transition-colors duration-150',
        selected
          ? 'border-accent bg-accent/8'
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

        {/* Pinned badge — shown when a map-pinned coordinate is stored */}
        {address.latitude != null && (
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-ink-tertiary">
            <MapPinned className="size-3 shrink-0" aria-hidden="true" />
            Pinned
          </span>
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
