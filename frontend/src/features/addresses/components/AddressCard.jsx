import { Home, Briefcase, MapPin, MapPinned, Star, Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/storefront/ui/Button.jsx';
import { Badge } from '@/components/storefront/ui/Badge.jsx';
import { cn } from '@/lib/utils.js';

// Keys match the API's lowercase AddressLabel values ("home"/"work"/"other").
const LABEL_META = {
  home:  { icon: Home,      tone: 'accent',   iconBg: 'bg-wgreen/10 text-wgreen'  },
  work:  { icon: Briefcase, tone: 'info',      iconBg: 'bg-wgreen/10 text-wgreen'  },
  other: { icon: MapPin,    tone: 'neutral',   iconBg: 'bg-wpaper text-wmuted'      },
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
        'min-w-0 rounded-xl border transition-colors duration-150',
        selected
          ? 'border-wgreen bg-wgreen/10'
          : 'border-wline bg-wcard',
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
          <span className="inline-flex items-center gap-1 text-[10px] font-medium text-wmuted">
            <MapPinned className="size-3 shrink-0" aria-hidden="true" />
            Pinned
          </span>
        )}
      </div>

      {/* Address body */}
      <p className={cn('mt-2.5 font-semibold text-wink', compact ? 'text-xs' : 'text-sm')}>
        {address.full_name}
      </p>
      <p className={cn('mt-0.5 text-wmuted', compact ? 'text-xs' : 'text-sm')}>
        {address.line1}
        {address.line2 ? `, ${address.line2}` : ''}
        {address.landmark ? ` (${address.landmark})` : ''}
      </p>
      <p className={cn('text-wmuted', compact ? 'text-xs' : 'text-sm')}>
        {address.city}, {address.state}{' '}
        <span className="nums">– {address.pincode}</span>
      </p>
      <p
        className={cn(
          'mt-0.5 nums text-wmuted',
          compact ? 'text-[11px]' : 'text-xs',
        )}
      >
        {address.phone}
      </p>

      {/* Action buttons */}
      {(onEdit || onDelete || onSetDefault) && (
        <div className="mt-3 flex flex-wrap items-center gap-1 border-t border-wline pt-3">
          {onEdit && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              iconOnly
              aria-label={`Edit address for ${address.full_name}`}
              onClick={() => onEdit(address)}
              className=""
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
              className="text-wmuted hover:text-wgreen"
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
              className="ml-auto text-wmuted hover:bg-red-50 hover:text-red-600"
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
