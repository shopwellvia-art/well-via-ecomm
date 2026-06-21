import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import {
  ArrowLeft,
  Package,
  Truck,
  CheckCircle2,
  XCircle,
  RotateCcw,
  Mail,
  ShieldCheck,
  Clock,
  CreditCard,
  TicketPercent,
  AlertTriangle,
  Save,
  Send,
  Calendar,
  Printer,
  RefreshCw,
  Activity,
  FlaskConical,
  MapPin,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import {
  useAdminOrder,
  useCancelOrder,
  useCancelShipment,
  useDeliverOrder,
  useMockSimulate,
  usePushToCarrier,
  useRefundOrder,
  useSchedulePickup,
  useShipOrder,
  useSyncTracking,
  useUpdateOrderNotes,
} from '@/features/admin-orders/hooks.js';
import { adminOrdersApi } from '@/features/admin-orders/api.js';
import { staggerContainer, fadeUp, scaleIn, slideInRight } from '@/lib/motion.js';

/** Maps order status to a Badge tone */
const STATUS_TONE = {
  pending:   'neutral',
  paid:      'accent',
  shipped:   'info',
  delivered: 'success',
  cancelled: 'warning',
  refunded:  'danger',
};

function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// Visual mapping of normalized TrackingStatus -> Badge tone
const TRACKING_TONE = {
  created:          'neutral',
  picked_up:        'info',
  in_transit:       'info',
  out_for_delivery: 'accent',
  delivered:        'success',
  failed:           'warning',
  returned:         'warning',
  cancelled:        'danger',
};

function humanizeStatus(s) {
  return (s || '').replace(/_/g, ' ');
}

function SectionLabel({ icon: Icon, children }) {
  return (
    <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
      {Icon && <Icon className="size-3.5 shrink-0" aria-hidden="true" />}
      {children}
    </p>
  );
}

function Timeline({ order }) {
  const rows = [
    { label: 'Placed',    when: order.created_at,   active: true },
    { label: 'Paid',      when: order.paid_at,      active: !!order.paid_at },
    { label: 'Shipped',   when: order.shipped_at,   active: !!order.shipped_at },
    { label: 'Delivered', when: order.delivered_at, active: !!order.delivered_at },
  ];
  if (order.cancelled_at) {
    rows.push({ label: 'Cancelled', when: order.cancelled_at, active: true, tone: 'warning' });
  }
  if (order.refunded_at) {
    rows.push({ label: 'Refunded', when: order.refunded_at, active: true, tone: 'danger' });
  }

  return (
    <ol className="relative flex flex-col gap-0">
      {rows.map((r, i) => (
        <li key={r.label} className="relative flex gap-3 pb-4 last:pb-0">
          {/* Vertical connector line */}
          {i < rows.length - 1 && (
            <span
              className={cn(
                'absolute left-[9px] top-5 h-full w-px',
                r.active ? 'bg-line-strong' : 'bg-line-subtle',
              )}
              aria-hidden="true"
            />
          )}
          {/* Dot */}
          <span
            className={cn(
              'relative z-10 mt-0.5 grid size-[18px] shrink-0 place-items-center rounded-full ring-2 ring-bg-elevated',
              r.active
                ? r.tone === 'warning'
                  ? 'bg-warning'
                  : r.tone === 'danger'
                  ? 'bg-danger'
                  : 'bg-accent'
                : 'bg-bg-sunken border border-line-strong',
            )}
            aria-hidden="true"
          />
          <div className={cn('flex-1', !r.active && 'opacity-40')}>
            <p className="text-sm font-medium text-ink-primary">{r.label}</p>
            <p className="nums mt-0.5 text-xs text-ink-tertiary">
              {r.active ? formatDateTime(r.when) : 'Not yet'}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function ShipForm({ order, onSuccess }) {
  const [tracking, setTracking] = useState(order.tracking_number || '');
  const [carrier, setCarrier] = useState(order.carrier || '');
  const [error, setError] = useState(null);
  const ship = useShipOrder();

  async function submit(e) {
    e.preventDefault();
    setError(null);
    try {
      await ship.mutateAsync({
        id: order.id,
        data: {
          tracking_number: tracking.trim() || null,
          carrier: carrier.trim() || null,
        },
      });
      onSuccess?.();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not ship the order.');
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-lg border border-line-subtle bg-bg-sunken p-4"
    >
      <p className="text-sm font-semibold text-ink-primary">Mark as shipped</p>
      <p className="mt-0.5 text-xs text-ink-tertiary">
        Optional but recommended: paste the carrier&apos;s tracking number so the
        customer can follow it.
      </p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Input
          label="Carrier"
          placeholder="UPS, FedEx, Delhivery…"
          value={carrier}
          onChange={(e) => setCarrier(e.target.value)}
        />
        <Input
          label="Tracking number"
          placeholder="1Z..."
          value={tracking}
          onChange={(e) => setTracking(e.target.value)}
        />
      </div>
      {error && (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-danger">
          <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
      <div className="mt-3 flex justify-end">
        <Button type="submit" size="sm" loading={ship.isPending}>
          <Truck className="size-4" aria-hidden="true" /> Ship it
        </Button>
      </div>
    </form>
  );
}

function ReasonModal({ title, action, onClose, onSubmit, pending }) {
  const [reason, setReason] = useState('');
  const [error, setError] = useState(null);
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 backdrop-blur-sm"
    >
      <motion.div
        variants={scaleIn}
        initial="hidden"
        animate="show"
        className="w-full max-w-md"
      >
        <Card className="p-6 shadow-lg">
          <p className="text-h3 font-semibold text-ink-primary">{title}</p>
          <p className="mt-1 text-xs text-ink-tertiary">
            A short reason is recorded in the audit log + on the order. The
            customer is not shown this verbatim.
          </p>
          <div className="mt-4">
            <Textarea
              label="Reason"
              required
              placeholder="Customer requested cancellation; item out of stock; etc."
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
            />
          </div>
          {error && (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-danger">
              <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
              {error}
            </p>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose} disabled={pending}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              loading={pending}
              onClick={() => {
                if (reason.trim().length < 3) {
                  setError('Reason is required (at least 3 characters).');
                  return;
                }
                onSubmit(reason.trim());
              }}
            >
              {action}
            </Button>
          </div>
        </Card>
      </motion.div>
    </motion.div>
  );
}

function defaultPickupDate() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function ShipmentPanel({ order }) {
  const push = usePushToCarrier();
  const schedule = useSchedulePickup();
  const sync = useSyncTracking();
  const cancelShipment = useCancelShipment();
  const [error, setError] = useState(null);
  const [pickupDate, setPickupDate] = useState(defaultPickupDate);
  const [labelLoading, setLabelLoading] = useState(false);
  const [localLabelLoading, setLocalLabelLoading] = useState(false);

  const hasAwb = !!order.shipping_awb;
  const hasPickup = !!order.pickup_id;
  const canPush = !hasAwb && order.status === 'paid';

  async function handlePush() {
    setError(null);
    try {
      await push.mutateAsync(order.id);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not push to carrier.');
    }
  }

  async function handleSchedule() {
    setError(null);
    if (!pickupDate) { setError('Pick a date first.'); return; }
    try {
      const iso = new Date(`${pickupDate}T12:00:00Z`).toISOString();
      await schedule.mutateAsync({ id: order.id, pickup_date: iso });
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not schedule pickup.');
    }
  }

  async function handleSync() {
    setError(null);
    try {
      await sync.mutateAsync(order.id);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not sync tracking.');
    }
  }

  async function handleCancelShipment() {
    if (!window.confirm('Cancel this shipment with the carrier? This cannot be undone.')) return;
    setError(null);
    try {
      await cancelShipment.mutateAsync(order.id);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not cancel the shipment.');
    }
  }

  async function handleLabel() {
    setError(null);
    setLabelLoading(true);
    try {
      const blob = await adminOrdersApi.fetchLabel(order.id);
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener,noreferrer');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not fetch the label.');
    } finally {
      setLabelLoading(false);
    }
  }

  async function handleLocalLabel() {
    setError(null);
    setLocalLabelLoading(true);
    try {
      const blob = await adminOrdersApi.fetchLocalLabel(order.id);
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener,noreferrer');
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not generate the label.');
    } finally {
      setLocalLabelLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader title={<SectionLabel icon={Truck}>Carrier shipment</SectionLabel>} />
      <div className="p-5">
        {hasAwb ? (
          <div className="space-y-4">
            <div>
              <div className="flex items-center justify-between gap-2">
                <span className="rounded-full bg-accent/12 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-accent">
                  {order.shipping_provider}
                </span>
                <span className="nums text-[11px] text-ink-tertiary">
                  {formatDateTime(order.shipment_created_at)}
                </span>
              </div>
              <p className="mt-1.5 break-all font-mono text-xs text-ink-primary">
                {order.shipping_awb}
              </p>
            </div>

            <Button size="sm" variant="secondary" block onClick={handleLabel} loading={labelLoading}>
              <Printer className="size-4" aria-hidden="true" /> Print label
            </Button>

            <Button
              size="sm"
              variant="ghost"
              block
              onClick={handleCancelShipment}
              loading={cancelShipment.isPending}
              className="text-danger hover:bg-danger/8 hover:text-danger"
            >
              <XCircle className="size-4" aria-hidden="true" /> Cancel shipment
            </Button>

            <div className="border-t border-line-subtle pt-4">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
                Pickup
              </p>
              {hasPickup ? (
                <div className="mt-2 text-xs text-ink-secondary">
                  <p className="nums font-mono text-ink-primary">{order.pickup_id}</p>
                  <p className="mt-0.5 inline-flex items-center gap-1.5">
                    <Calendar className="size-3" aria-hidden="true" />
                    {formatDateTime(order.pickup_scheduled_for)}
                  </p>
                </div>
              ) : (
                <div className="mt-2 flex items-center gap-2">
                  <input
                    type="date"
                    value={pickupDate}
                    onChange={(e) => setPickupDate(e.target.value)}
                    min={defaultPickupDate()}
                    className="flex-1 rounded-sm border border-line-subtle bg-bg-elevated px-2 py-1.5 text-xs text-ink-primary focus-visible:focus-ring"
                  />
                  <Button size="sm" onClick={handleSchedule} loading={schedule.isPending}>
                    Schedule
                  </Button>
                </div>
              )}
            </div>

            <div className="border-t border-line-subtle pt-4">
              <div className="flex items-center justify-between">
                <SectionLabel icon={Activity}>Tracking</SectionLabel>
                <button
                  type="button"
                  onClick={handleSync}
                  disabled={sync.isPending}
                  className="inline-flex items-center gap-1 text-[11px] text-accent transition-colors hover:underline disabled:opacity-50 focus-visible:focus-ring"
                >
                  <RefreshCw className={cn('size-3', sync.isPending && 'animate-spin')} aria-hidden="true" />
                  Sync
                </button>
              </div>
              {order.last_tracking_at && (
                <p className="mt-1 nums text-[10px] text-ink-tertiary">
                  Last update {formatDateTime(order.last_tracking_at)}
                </p>
              )}
              <div className="mt-3">
                <TrackingTimeline events={order.tracking_events} />
              </div>
              {order.shipping_provider === 'mock' && <MockSimulator order={order} />}
            </div>

            {error && (
              <p className="flex items-center gap-1.5 text-xs text-danger">
                <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
                {error}
              </p>
            )}
          </div>
        ) : canPush ? (
          <div>
            <p className="text-xs text-ink-secondary">
              Push this order to the active shipping provider to mint a waybill.
            </p>
            {error && (
              <p className="mt-2 flex items-center gap-1.5 text-xs text-danger">
                <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
                {error}
              </p>
            )}
            <Button size="sm" className="mt-3" onClick={handlePush} loading={push.isPending}>
              <Send className="size-4" aria-hidden="true" /> Push to carrier
            </Button>
          </div>
        ) : (
          <p className="text-xs text-ink-tertiary">
            {order.status === 'paid'
              ? 'Configure a shipping provider in Settings to push this order.'
              : `Available once the order reaches PAID (currently ${order.status}).`}
          </p>
        )}

        {/* In-house 4x6 label — always available (no live carrier/AWB needed). */}
        <div className="mt-4 border-t border-line-subtle pt-4">
          <Button
            size="sm"
            variant="outline"
            block
            onClick={handleLocalLabel}
            loading={localLabelLoading}
          >
            <Printer className="size-4" aria-hidden="true" /> Download label (4x6)
          </Button>
          <p className="mt-1.5 text-[10px] text-ink-tertiary">
            In-house label generated from this order. {hasAwb ? 'Use “Print label” above for the carrier’s official label.' : 'A waybill barcode appears here once the order is pushed to the carrier.'}
          </p>
        </div>
      </div>
    </Card>
  );
}

function TrackingTimeline({ events }) {
  if (!events || events.length === 0) {
    return (
      <p className="text-xs text-ink-tertiary">
        No tracking events yet. The carrier will push updates here.
      </p>
    );
  }
  const ordered = [...events].sort((a, b) =>
    (b.occurred_at || '').localeCompare(a.occurred_at || ''),
  );
  return (
    <ol className="flex flex-col gap-2">
      {ordered.map((e, i) => (
        <li
          key={`${e.status}-${e.occurred_at}-${i}`}
          className="flex items-start gap-2.5 rounded-md border border-line-subtle bg-bg-sunken px-3 py-2.5 text-xs"
        >
          <Badge tone={TRACKING_TONE[e.status] ?? 'neutral'} size="sm">
            {humanizeStatus(e.status)}
          </Badge>
          <div className="min-w-0 flex-1">
            <p className="text-ink-primary">{e.note || e.location || '—'}</p>
            <p className="nums mt-0.5 text-[10px] text-ink-tertiary">
              {formatDateTime(e.occurred_at)}
              {e.location && e.note ? ` · ${e.location}` : ''}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function MockSimulator({ order }) {
  const [status, setStatus] = useState('in_transit');
  const sim = useMockSimulate();
  const [error, setError] = useState(null);

  async function fire() {
    setError(null);
    try {
      await sim.mutateAsync({
        id: order.id,
        awb: order.shipping_awb,
        status,
        note: `Simulated ${status}`,
      });
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Simulation failed.');
    }
  }

  return (
    <div className="mt-4 rounded-md border border-dashed border-line-strong bg-bg-elevated px-3 py-3">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
        <FlaskConical className="size-3" aria-hidden="true" /> Mock simulator
      </p>
      <div className="mt-2 flex items-center gap-2">
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="flex-1 rounded-sm border border-line-subtle bg-bg-sunken px-2 py-1.5 text-xs text-ink-primary focus-visible:focus-ring"
        >
          {['picked_up', 'in_transit', 'out_for_delivery', 'delivered', 'returned', 'failed'].map((s) => (
            <option key={s} value={s}>{humanizeStatus(s)}</option>
          ))}
        </select>
        <Button size="sm" variant="secondary" onClick={fire} loading={sim.isPending}>
          Fire
        </Button>
      </div>
      {error && <p className="mt-1.5 text-[11px] text-danger">{error}</p>}
    </div>
  );
}

function NotesPanel({ order }) {
  const [notes, setNotes] = useState(order.internal_notes || '');
  const [savedAt, setSavedAt] = useState(null);
  const update = useUpdateOrderNotes();
  const dirty = (notes || '') !== (order.internal_notes || '');

  async function save() {
    try {
      await update.mutateAsync({ id: order.id, internal_notes: notes });
      setSavedAt(Date.now());
      setTimeout(() => setSavedAt(null), 1500);
    } catch (_err) {
      /* mutation surfaces error state */
    }
  }

  return (
    <Card>
      <CardHeader
        title="Internal notes"
        action={
          <span className="text-[11px] text-ink-tertiary">Visible to staff only</span>
        }
      />
      <div className="p-5">
        <Textarea
          rows={4}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Talk to fulfillment about repackaging; customer prefers…"
          maxRows={10}
        />
        <div className="mt-3 flex items-center justify-between">
          <p className="text-xs text-ink-tertiary">
            {savedAt ? (
              <span className="inline-flex items-center gap-1.5 text-success">
                <CheckCircle2 className="size-3.5" aria-hidden="true" /> Saved
              </span>
            ) : dirty ? (
              <span className="text-warning">Unsaved changes</span>
            ) : null}
          </p>
          <Button size="sm" onClick={save} disabled={!dirty} loading={update.isPending}>
            <Save className="size-4" aria-hidden="true" /> Save notes
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ── Payment status tone map ───────────────────────────────────────────────────
const PAYMENT_STATUS_TONE = {
  paid: 'success',
  authorized: 'info',
  initiated: 'warning',
  pending: 'warning',
  failed: 'danger',
  cancelled: 'danger',
  refunded: 'neutral',
  partially_refunded: 'neutral',
};

// ── Shipment status tone map ──────────────────────────────────────────────────
const SHIPMENT_STATUS_TONE = {
  pending: 'neutral',
  ready_to_ship: 'info',
  pickup_scheduled: 'info',
  shipped: 'info',
  in_transit: 'info',
  out_for_delivery: 'accent',
  delivered: 'success',
  delivery_failed: 'danger',
  rto_initiated: 'warning',
  rto_delivered: 'warning',
  cancelled: 'danger',
};

/** Render one address object (normalized addresses[] or legacy snapshot shape) */
function AddressBlock({ addr }) {
  if (!addr) return <p className="text-sm text-ink-tertiary">No address on record.</p>;
  // addresses[] uses address_line1/address_line2; snapshots use line1/line2
  const name = addr.full_name || addr.name || null;
  const line1 = addr.address_line1 || addr.line1 || null;
  const line2 = addr.address_line2 || addr.line2 || null;
  const city = addr.city || null;
  const state = addr.state || null;
  const pincode = addr.pincode || null;
  const country = addr.country || null;
  const phone = addr.phone || null;
  const landmark = addr.landmark || null;
  const email = addr.email || null;
  const gst = addr.gst_number || null;

  const lines = [
    name,
    line1,
    line2,
    landmark,
    [city, state, pincode].filter(Boolean).join(', '),
    country,
    phone ? `Phone: ${phone}` : null,
    email ? `Email: ${email}` : null,
    gst ? `GST: ${gst}` : null,
  ].filter(Boolean);

  return (
    <address className="not-italic space-y-0.5">
      {lines.map((l, i) => (
        <p
          key={i}
          className={
            i === 0
              ? 'text-sm font-semibold text-ink-primary'
              : 'text-sm text-ink-secondary'
          }
        >
          {l}
        </p>
      ))}
    </address>
  );
}

/**
 * Payment details panel — shows normalized payments[] rows.
 * Falls back to the legacy payment_intent_id field when the array is empty.
 */
function PaymentDetailsPanel({ order }) {
  const payments = order.payments ?? [];

  return (
    <Card>
      <CardHeader
        title={<SectionLabel icon={CreditCard}>Payment details</SectionLabel>}
      />
      <div className="p-5">
        {/* Always show legacy intent ID if present */}
        {order.payment_intent_id && (
          <div className="mb-4 rounded-sm border border-line-subtle bg-bg-sunken px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
              Payment intent
            </p>
            <p className="nums mt-0.5 break-all font-mono text-xs text-ink-primary">
              {order.payment_intent_id}
            </p>
          </div>
        )}

        {payments.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-xs">
              <thead>
                <tr className="border-b border-line-subtle text-left">
                  <th className="pb-2 font-semibold uppercase tracking-wide text-ink-tertiary">Gateway</th>
                  <th className="pb-2 font-semibold uppercase tracking-wide text-ink-tertiary">Method</th>
                  <th className="pb-2 font-semibold uppercase tracking-wide text-ink-tertiary">Status</th>
                  <th className="pb-2 text-right font-semibold uppercase tracking-wide text-ink-tertiary">Amount</th>
                  <th className="pb-2 font-semibold uppercase tracking-wide text-ink-tertiary">Reference</th>
                  <th className="pb-2 font-semibold uppercase tracking-wide text-ink-tertiary">Paid at</th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p) => (
                  <tr key={p.id} className="border-t border-line-subtle">
                    <td className="py-2.5 pr-3 capitalize text-ink-secondary">
                      {p.gateway || '—'}
                    </td>
                    <td className="py-2.5 pr-3 capitalize text-ink-secondary">
                      {(p.payment_method || '—').replace(/_/g, ' ')}
                    </td>
                    <td className="py-2.5 pr-3">
                      <Badge
                        tone={PAYMENT_STATUS_TONE[p.payment_status] ?? 'neutral'}
                        size="sm"
                        className="capitalize"
                      >
                        {(p.payment_status || '').replace(/_/g, ' ')}
                      </Badge>
                    </td>
                    <td className="py-2.5 pr-3 text-right nums font-semibold text-ink-primary">
                      {formatPrice(p.amount, p.currency || order.currency)}
                    </td>
                    <td className="py-2.5 pr-3 font-mono text-ink-tertiary">
                      {p.transaction_reference || '—'}
                    </td>
                    <td className="py-2.5 text-ink-tertiary whitespace-nowrap">
                      {p.paid_at ? formatDateTime(p.paid_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-ink-tertiary">
            {order.payment_intent_id
              ? 'No normalized payment rows yet.'
              : 'No payment details recorded.'}
          </p>
        )}
      </div>
    </Card>
  );
}

/**
 * Shipment details panel — shows normalized shipments[] rows.
 * Complements the existing ShipmentPanel (carrier operations) which stays above it.
 */
function ShipmentDetailsPanel({ order }) {
  const shipments = order.shipments ?? [];

  if (shipments.length === 0) {
    return null; // no-op if backend returns nothing yet
  }

  return (
    <Card>
      <CardHeader
        title={<SectionLabel icon={Truck}>Shipment details</SectionLabel>}
      />
      <div className="p-5 space-y-4">
        {shipments.map((s, idx) => {
          const awb = s.awb_number || s.tracking_number || null;
          return (
            <div
              key={s.id ?? idx}
              className={cn(
                'rounded-sm border border-line-subtle bg-bg-sunken p-4',
                idx > 0 && 'mt-4',
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-ink-primary capitalize">
                    {s.courier_partner || 'Courier'}
                  </span>
                  {s.courier_service && (
                    <span className="text-[11px] text-ink-tertiary">{s.courier_service}</span>
                  )}
                  <Badge
                    tone={SHIPMENT_STATUS_TONE[s.shipment_status] ?? 'neutral'}
                    size="sm"
                    className="capitalize"
                  >
                    {(s.shipment_status || '').replace(/_/g, ' ')}
                  </Badge>
                </div>
                {awb && (
                  <code className="font-mono text-xs text-ink-secondary">{awb}</code>
                )}
              </div>

              {/* Per-leg timestamps */}
              <div className="mt-3 grid gap-1.5 text-[11px] text-ink-tertiary sm:grid-cols-2">
                {s.pickup_scheduled_at && (
                  <span>Pickup scheduled: {formatDateTime(s.pickup_scheduled_at)}</span>
                )}
                {s.shipped_at && (
                  <span>Shipped: {formatDateTime(s.shipped_at)}</span>
                )}
                {s.in_transit_at && (
                  <span>In transit: {formatDateTime(s.in_transit_at)}</span>
                )}
                {s.out_for_delivery_at && (
                  <span>Out for delivery: {formatDateTime(s.out_for_delivery_at)}</span>
                )}
                {s.delivered_at && (
                  <span>Delivered: {formatDateTime(s.delivered_at)}</span>
                )}
                {s.failed_delivery_at && (
                  <span>Delivery failed: {formatDateTime(s.failed_delivery_at)}</span>
                )}
                {s.returned_at && (
                  <span>Returned: {formatDateTime(s.returned_at)}</span>
                )}
              </div>

              {s.tracking_url && (
                <a
                  href={s.tracking_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-flex items-center gap-1 text-xs text-accent hover:underline focus-visible:focus-ring"
                >
                  Track on courier site
                </a>
              )}

              <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-tertiary">
                {s.shipment_cost != null && (
                  <div className="flex gap-1">
                    <dt>Cost:</dt>
                    <dd className="nums">{formatPrice(s.shipment_cost, order.currency)}</dd>
                  </div>
                )}
                {s.package_weight_grams != null && (
                  <div className="flex gap-1">
                    <dt>Weight:</dt>
                    <dd className="nums">{s.package_weight_grams}g</dd>
                  </div>
                )}
              </dl>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

/**
 * Address snapshot panel — renders shipping + billing from normalized addresses[].
 * When the array is empty, falls back to the legacy snapshot objects and
 * the order.shipping_address string, exactly as the original code did.
 */
function AddressSnapshotPanel({ order }) {
  const addresses = order.addresses ?? [];
  const shippingNorm = addresses.find((a) => a.address_type === 'shipping') ?? null;
  const billingNorm = addresses.find((a) => a.address_type === 'billing') ?? null;

  // Legacy snapshot fallback
  const shippingSnap = order.shipping_address_snapshot ?? null;
  const billingSnap = order.billing_address_snapshot ?? null;

  const shippingSource = shippingNorm || shippingSnap;
  const billingSource = billingNorm || billingSnap;

  // Determine if billing differs from shipping (for "same as shipping" label)
  const billingDiffers = billingSource
    ? order.billing_address_id != null &&
      order.billing_address_id !== order.shipping_address_id
      ? true
      : JSON.stringify(shippingSnap) !== JSON.stringify(billingSnap)
    : false;

  return (
    <>
      {/* Shipping address */}
      <Card>
        <CardHeader title={<SectionLabel icon={MapPin}>Shipping address</SectionLabel>} />
        <div className="p-5">
          {shippingSource ? (
            <AddressBlock addr={shippingSource} />
          ) : order.shipping_address ? (
            <p className="whitespace-pre-line text-sm text-ink-primary">
              {order.shipping_address}
            </p>
          ) : (
            <p className="text-sm text-ink-tertiary">No address on record.</p>
          )}
        </div>
      </Card>

      {/* Billing address — shown only when there's something to show */}
      {(billingSource || order.billing_address_id != null) && (
        <Card>
          <CardHeader title={<SectionLabel icon={MapPin}>Billing address</SectionLabel>} />
          <div className="p-5">
            {!billingDiffers ? (
              <p className="text-sm italic text-ink-tertiary">Same as delivery address.</p>
            ) : billingSource ? (
              <AddressBlock addr={billingSource} />
            ) : (
              <p className="text-sm text-ink-tertiary">No billing address on record.</p>
            )}
          </div>
        </Card>
      )}
    </>
  );
}

export default function AdminOrderDetailPage() {
  const { id } = useParams();
  const orderId = Number(id);
  const { data: order, isLoading, isError } = useAdminOrder(orderId);

  const deliver = useDeliverOrder();
  const cancel = useCancelOrder();
  const refund = useRefundOrder();

  const [modal, setModal] = useState(null); // 'cancel' | 'refund' | null

  if (isLoading) {
    return (
      <AdminPage title="Order">
        <div className="max-w-content space-y-4">
          <Skeleton className="h-8 w-36" />
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="space-y-4">
              <Skeleton className="h-40 rounded-lg" />
              <Skeleton className="h-64 rounded-lg" />
              <Skeleton className="h-28 rounded-lg" />
            </div>
            <div className="space-y-4">
              <Skeleton className="h-24 rounded-lg" />
              <Skeleton className="h-24 rounded-lg" />
              <Skeleton className="h-40 rounded-lg" />
            </div>
          </div>
        </div>
      </AdminPage>
    );
  }

  if (isError || !order) {
    return (
      <AdminPage title="Order">
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          title="Order not found"
          description="It may have been deleted or the ID is invalid."
          action={
            <Link to="/admin/orders">
              <Button size="sm">
                <ArrowLeft className="size-4" aria-hidden="true" /> Back to orders
              </Button>
            </Link>
          }
        />
      </AdminPage>
    );
  }

  const canShip    = order.status === 'paid';
  const canDeliver = order.status === 'shipped';
  const canCancel  = order.status === 'paid';
  const canRefund  = ['paid', 'shipped', 'delivered'].includes(order.status);

  return (
    <AdminPage
      title={order.order_number ? `${order.order_number} · #${order.id}` : `Order #${order.id}`}
      description={`Placed ${formatDateTime(order.created_at)} by ${order.customer.email}`}
    >
      <Link
        to="/admin/orders"
        className="mb-5 inline-flex items-center gap-1.5 rounded-sm text-sm text-ink-secondary transition-colors hover:text-ink-primary focus-visible:focus-ring"
      >
        <ArrowLeft className="size-4" aria-hidden="true" />
        All orders
      </Link>

      <motion.div
        variants={staggerContainer(0.06)}
        initial="hidden"
        animate="show"
        className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]"
      >
        {/* ── Main column ── */}
        <div className="flex flex-col gap-6">
          {/* Status + actions header */}
          <motion.div variants={fadeUp}>
            <Card>
              <div className="flex flex-wrap items-start justify-between gap-4 p-5">
                <div>
                  <div className="flex items-center gap-2">
                    <Badge tone={STATUS_TONE[order.status] ?? 'neutral'} size="md" dot>
                      {order.status}
                    </Badge>
                    {order.refund_reason && (
                      <span className="text-xs text-ink-tertiary">
                        ({order.refund_reason})
                      </span>
                    )}
                  </div>
                  <p className="mt-2 nums text-h3 font-semibold text-ink-primary">
                    {formatPrice(order.total_amount, order.currency)}
                  </p>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  {canDeliver && (
                    <Button
                      size="sm"
                      onClick={() => deliver.mutate(orderId)}
                      loading={deliver.isPending}
                    >
                      <CheckCircle2 className="size-4" aria-hidden="true" /> Mark delivered
                    </Button>
                  )}
                  {canCancel && (
                    <Button size="sm" variant="ghost" onClick={() => setModal('cancel')}>
                      <XCircle className="size-4" aria-hidden="true" /> Cancel
                    </Button>
                  )}
                  {canRefund && (
                    <Button size="sm" variant="ghost" onClick={() => setModal('refund')}>
                      <RotateCcw className="size-4" aria-hidden="true" /> Refund
                    </Button>
                  )}
                </div>
              </div>

              {canShip && (
                <div className="border-t border-line-subtle p-5">
                  <ShipForm order={order} />
                </div>
              )}

              {(order.tracking_number || order.carrier) && !canShip && (
                <div className="border-t border-line-subtle px-5 pb-5">
                  <div className="mt-4 rounded-md border border-line-subtle bg-bg-sunken px-3 py-2.5 text-xs">
                    <p className="font-semibold text-ink-primary">
                      {order.carrier ? order.carrier : 'Shipped'}
                    </p>
                    {order.tracking_number && (
                      <p className="nums mt-0.5 font-mono text-ink-secondary">{order.tracking_number}</p>
                    )}
                  </div>
                </div>
              )}
            </Card>
          </motion.div>

          {/* Line items + totals */}
          <motion.div variants={fadeUp}>
            <Card>
              <CardHeader title="Line items" />
              <div className="p-5">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-line-subtle text-left">
                      <th className="pb-2.5 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                        Product
                      </th>
                      <th className="pb-2.5 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                        Qty
                      </th>
                      <th className="pb-2.5 text-right text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                        Unit
                      </th>
                      <th className="pb-2.5 text-right text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                        Line total
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {order.items.map((it) => (
                      <tr key={it.id} className="border-t border-line-subtle">
                        <td className="py-3 text-sm text-ink-primary">
                          <Link
                            to={`/admin/products/${it.product_id}/edit`}
                            className="hover:text-accent hover:underline focus-visible:focus-ring"
                          >
                            Product #{it.product_id}
                          </Link>
                        </td>
                        <td className="py-3 nums text-sm text-ink-secondary">
                          ×{it.quantity}
                        </td>
                        <td className="py-3 text-right nums text-sm text-ink-secondary">
                          {formatPrice(it.unit_price, order.currency)}
                        </td>
                        <td className="py-3 text-right nums text-sm font-semibold text-ink-primary">
                          {formatPrice(Number(it.unit_price) * it.quantity, order.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <dl className="mt-4 space-y-1.5 border-t border-line-subtle pt-4 text-sm">
                  <div className="flex justify-between text-ink-secondary">
                    <dt>Subtotal</dt>
                    <dd className="nums text-ink-primary">{formatPrice(order.subtotal, order.currency)}</dd>
                  </div>
                  {Number(order.tax_amount) > 0 && (
                    <div className="flex justify-between text-ink-secondary">
                      <dt>Tax</dt>
                      <dd className="nums text-ink-primary">{formatPrice(order.tax_amount, order.currency)}</dd>
                    </div>
                  )}
                  {Number(order.discount_amount) > 0 && (
                    <div className="flex justify-between text-success">
                      <dt className="inline-flex items-center gap-1.5">
                        <TicketPercent className="size-3.5" aria-hidden="true" />
                        Discount
                        {order.coupon_code && (
                          <span className="font-mono text-[10px] text-ink-tertiary">
                            ({order.coupon_code})
                          </span>
                        )}
                      </dt>
                      <dd className="nums">−{formatPrice(order.discount_amount, order.currency)}</dd>
                    </div>
                  )}
                  <div className="flex justify-between border-t border-line-subtle pt-3 text-ink-primary">
                    <dt className="font-semibold">Total</dt>
                    <dd className="nums text-h3 font-semibold">{formatPrice(order.total_amount, order.currency)}</dd>
                  </div>
                </dl>
              </div>
            </Card>
          </motion.div>

          <motion.div variants={fadeUp}>
            <NotesPanel order={order} />
          </motion.div>
        </div>

        {/* ── Side column ── */}
        <motion.div variants={slideInRight} className="flex flex-col gap-5">
          {/* Customer */}
          <Card>
            <CardHeader title={<SectionLabel icon={Mail}>Customer</SectionLabel>} />
            <div className="p-5">
              <p className="text-sm font-semibold text-ink-primary">
                {order.customer.email}
              </p>
              {order.customer.full_name && (
                <p className="mt-0.5 text-xs text-ink-secondary">
                  {order.customer.full_name}
                </p>
              )}
              <Link
                to={`/admin/users?q=${encodeURIComponent(order.customer.email)}`}
                className="mt-3 inline-flex items-center gap-1 text-xs text-accent hover:underline focus-visible:focus-ring"
              >
                View customer profile →
              </Link>
            </div>
          </Card>

          {/* Address snapshot — normalized addresses[] with snapshot fallbacks */}
          <AddressSnapshotPanel order={order} />

          {/* Payment details — normalized payments[] with intent-id fallback */}
          <PaymentDetailsPanel order={order} />

          {/* Shipment details — normalized shipments[] */}
          <ShipmentDetailsPanel order={order} />

          {/* Carrier shipment */}
          <ShipmentPanel order={order} />

          {/* Timeline */}
          <Card>
            <CardHeader title={<SectionLabel icon={ShieldCheck}>Timeline</SectionLabel>} />
            <div className="p-5">
              <Timeline order={order} />
            </div>
          </Card>
        </motion.div>
      </motion.div>

      {/* Modals */}
      {modal === 'cancel' && (
        <ReasonModal
          title="Cancel order"
          action="Cancel order"
          pending={cancel.isPending}
          onClose={() => setModal(null)}
          onSubmit={(reason) => {
            cancel.mutate(
              { id: orderId, reason },
              { onSuccess: () => setModal(null) },
            );
          }}
        />
      )}
      {modal === 'refund' && (
        <ReasonModal
          title="Refund order"
          action="Issue refund"
          pending={refund.isPending}
          onClose={() => setModal(null)}
          onSubmit={(reason) => {
            refund.mutate(
              { id: orderId, reason },
              { onSuccess: () => setModal(null) },
            );
          }}
        />
      )}
    </AdminPage>
  );
}
