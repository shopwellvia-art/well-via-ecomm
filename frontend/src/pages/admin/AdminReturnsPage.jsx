import { useState } from 'react';
import { motion } from 'framer-motion';
import {
  Undo2,
  CheckCircle2,
  XCircle,
  Truck,
  PackageCheck,
  RefreshCcw,
  AlertTriangle,
} from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Card, CardHeader } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Textarea } from '@/components/ui/Textarea.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import {
  useAdminReturns,
  useApproveReturn,
  useMarkReturnPickedUp,
  useMarkReturnReceived,
  useMarkReturnRefunded,
  useRejectReturn,
} from '@/features/returns/hooks.js';
import { listStagger, fadeUp, slideInRight } from '@/lib/motion.js';

const STATUS_TONE = {
  requested: 'warning',
  approved:  'accent',
  rejected:  'danger',
  picked_up: 'info',
  received:  'info',
  refunded:  'success',
  cancelled: 'neutral',
};

const STATUS_OPTIONS = [
  { value: '',           label: 'All' },
  { value: 'requested',  label: 'Requested' },
  { value: 'approved',   label: 'Approved' },
  { value: 'picked_up',  label: 'Picked up' },
  { value: 'received',   label: 'Received' },
  { value: 'refunded',   label: 'Refunded' },
  { value: 'rejected',   label: 'Rejected' },
  { value: 'cancelled',  label: 'Cancelled' },
];

function formatDateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function SectionLabel({ icon: Icon, children }) {
  return (
    <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
      {Icon && <Icon className="size-3.5 shrink-0" aria-hidden="true" />}
      {children}
    </p>
  );
}

function ApprovePanel({ ret, onClose }) {
  const [refund, setRefund] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState(null);
  const approve = useApproveReturn();

  async function submit() {
    setError(null);
    try {
      await approve.mutateAsync({
        id: ret.id,
        admin_notes: notes.trim() || null,
        refund_amount: refund === '' ? null : Number(refund),
      });
      onClose();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not approve.');
    }
  }

  return (
    <Card className="shadow-md">
      <CardHeader title="Approve return" />
      <div className="p-5">
        <p className="mb-4 text-xs text-ink-tertiary">
          Approving mints a reverse waybill with the carrier. Leave the amount
          blank to refund the returned items&apos; subtotal.
        </p>
        <div className="space-y-4">
          <Input
            label="Refund amount"
            type="number"
            step="0.01"
            min="0"
            value={refund}
            onChange={(e) => setRefund(e.target.value)}
            placeholder="auto"
          />
          <Textarea
            label="Internal notes (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
          />
        </div>
        {error && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-danger">
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
            {error}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={approve.isPending}>
            Cancel
          </Button>
          <Button size="sm" onClick={submit} loading={approve.isPending}>
            <CheckCircle2 className="size-4" aria-hidden="true" /> Approve
          </Button>
        </div>
      </div>
    </Card>
  );
}

function RejectPanel({ ret, onClose }) {
  const [notes, setNotes] = useState('');
  const [error, setError] = useState(null);
  const reject = useRejectReturn();

  async function submit() {
    setError(null);
    if (notes.trim().length < 3) {
      setError('Add a short reason — at least 3 characters.');
      return;
    }
    try {
      await reject.mutateAsync({ id: ret.id, admin_notes: notes.trim() });
      onClose();
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Could not reject.');
    }
  }

  return (
    <Card className="shadow-md">
      <CardHeader title="Reject return" />
      <div className="p-5">
        <p className="mb-4 text-xs text-ink-tertiary">
          The customer won&apos;t see this note verbatim — it&apos;s recorded
          internally and on the audit log.
        </p>
        <Textarea
          label="Reason for rejection"
          required
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          placeholder="Past window per phone call; items appear unused; etc."
        />
        {error && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-danger">
            <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
            {error}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" size="sm" onClick={onClose} disabled={reject.isPending}>
            Cancel
          </Button>
          <Button variant="destructive" size="sm" onClick={submit} loading={reject.isPending}>
            <XCircle className="size-4" aria-hidden="true" /> Reject
          </Button>
        </div>
      </div>
    </Card>
  );
}

function ReturnRow({ ret, onSelect, selected }) {
  return (
    <motion.button
      variants={fadeUp}
      type="button"
      onClick={onSelect}
      className={cn(
        'w-full rounded-lg border px-4 py-3 text-left transition-all duration-150',
        selected
          ? 'border-accent bg-accent/8 shadow-glow-sm'
          : 'border-line-subtle bg-bg-elevated hover:border-line-strong hover:bg-fill/50',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-ink-primary">Return #{ret.id}</p>
          <p className="mt-0.5 text-[11px] text-ink-tertiary">
            Order #{ret.order_id} · {ret.customer.email}
          </p>
        </div>
        <Badge tone={STATUS_TONE[ret.status] ?? 'neutral'} dot>
          {ret.status.replace(/_/g, ' ')}
        </Badge>
      </div>
      <p className="mt-1.5 text-[11px] text-ink-tertiary">
        {ret.reason.replace(/_/g, ' ')} · {formatDateTime(ret.requested_at)}
      </p>
    </motion.button>
  );
}

function ReturnDetailPane({ ret }) {
  const [panel, setPanel] = useState(null); // 'approve' | 'reject' | null
  const pickedUp = useMarkReturnPickedUp();
  const received = useMarkReturnReceived();
  const refunded = useMarkReturnRefunded();
  const [error, setError] = useState(null);

  async function fire(mutation) {
    setError(null);
    try {
      await mutation.mutateAsync(ret.id);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Action failed.');
    }
  }

  return (
    <motion.div
      key={ret.id}
      variants={slideInRight}
      initial="hidden"
      animate="show"
      className="flex flex-col gap-5"
    >
      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <Undo2 className="size-4 text-accent" aria-hidden="true" />
              <span className="text-sm font-semibold text-ink-primary">Return #{ret.id}</span>
            </span>
          }
          action={
            <Badge tone={STATUS_TONE[ret.status] ?? 'neutral'} size="md" dot>
              {ret.status.replace(/_/g, ' ')}
            </Badge>
          }
        />
        <div className="p-5">
          <p className="text-xs text-ink-tertiary">
            Order #{ret.order_id} · {ret.customer.email}
          </p>

          {/* Meta grid */}
          <div className="mt-4 grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Reason</p>
              <p className="mt-0.5 text-sm text-ink-primary capitalize">
                {ret.reason.replace(/_/g, ' ')}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Refund</p>
              <p className="nums mt-0.5 text-sm font-semibold text-ink-primary">
                {ret.refund_amount != null ? formatPrice(ret.refund_amount) : '—'}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Requested</p>
              <p className="nums mt-0.5 text-sm text-ink-primary">
                {formatDateTime(ret.requested_at)}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Reverse AWB</p>
              <p className="nums mt-0.5 break-all font-mono text-xs text-ink-primary">
                {ret.reverse_awb || '—'}
              </p>
            </div>
          </div>

          {/* Customer notes */}
          {ret.customer_notes && (
            <div className="mt-4 rounded-md border border-line-subtle bg-bg-sunken px-3 py-3 text-xs">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
                From the customer
              </p>
              <p className="mt-1.5 whitespace-pre-line text-ink-primary">
                {ret.customer_notes}
              </p>
            </div>
          )}

          {/* Internal notes */}
          {ret.admin_notes && (
            <div className="mt-3 rounded-md border border-line-subtle bg-bg-sunken px-3 py-3 text-xs">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
                Internal notes
              </p>
              <p className="mt-1.5 whitespace-pre-line text-ink-primary">{ret.admin_notes}</p>
            </div>
          )}

          {/* Items */}
          <p className="mt-5 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
            Items
          </p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {ret.items.map((i) => (
              <li
                key={i.id}
                className="flex items-center justify-between rounded-md border border-line-subtle bg-bg-sunken px-3 py-2 text-xs"
              >
                <span className="text-ink-primary">Order item #{i.order_item_id}</span>
                <span className="nums text-ink-tertiary">× {i.quantity}</span>
              </li>
            ))}
          </ul>

          {/* Action buttons */}
          <div className="mt-5 flex flex-wrap gap-2">
            {ret.status === 'requested' && (
              <>
                <Button size="sm" onClick={() => setPanel('approve')}>
                  <CheckCircle2 className="size-4" aria-hidden="true" /> Approve
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setPanel('reject')}>
                  <XCircle className="size-4" aria-hidden="true" /> Reject
                </Button>
              </>
            )}
            {ret.status === 'approved' && (
              <Button size="sm" onClick={() => fire(pickedUp)} loading={pickedUp.isPending}>
                <Truck className="size-4" aria-hidden="true" /> Mark picked up
              </Button>
            )}
            {(ret.status === 'approved' || ret.status === 'picked_up') && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => fire(received)}
                loading={received.isPending}
              >
                <PackageCheck className="size-4" aria-hidden="true" /> Mark received
              </Button>
            )}
            {ret.status === 'received' && (
              <Button size="sm" onClick={() => fire(refunded)} loading={refunded.isPending}>
                <RefreshCcw className="size-4" aria-hidden="true" /> Mark refunded
              </Button>
            )}
          </div>

          {error && (
            <p className="mt-3 flex items-center gap-1.5 text-xs text-danger">
              <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
              {error}
            </p>
          )}
        </div>
      </Card>

      {panel === 'approve' && (
        <ApprovePanel ret={ret} onClose={() => setPanel(null)} />
      )}
      {panel === 'reject' && (
        <RejectPanel ret={ret} onClose={() => setPanel(null)} />
      )}
    </motion.div>
  );
}

export default function AdminReturnsPage() {
  const [statusFilter, setStatusFilter] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const { data: returns = [], isLoading } = useAdminReturns(statusFilter || null);

  const selected = returns.find((r) => r.id === selectedId) || null;

  return (
    <AdminPage
      title="Returns"
      description="Review customer return requests and walk them through pickup → received → refunded."
    >
      {/* Status filter chips */}
      <div className="mb-5 flex flex-wrap items-center gap-2">
        {STATUS_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => setStatusFilter(o.value)}
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-medium transition-colors focus-visible:focus-ring',
              statusFilter === o.value
                ? 'border-accent bg-accent/12 text-accent'
                : 'border-line-subtle bg-bg-elevated text-ink-secondary hover:border-line-strong hover:text-ink-primary',
            )}
          >
            {o.label}
          </button>
        ))}
      </div>

      <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
        {/* List pane */}
        <motion.div
          variants={listStagger(0.04)}
          initial="hidden"
          animate="show"
          className="flex flex-col gap-2"
        >
          {isLoading ? (
            Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-20 rounded-lg" />
            ))
          ) : returns.length === 0 ? (
            <EmptyState
              icon={Undo2}
              title="No returns"
              description={
                statusFilter
                  ? 'No returns match this filter.'
                  : 'No returns have been requested yet.'
              }
              size="sm"
            />
          ) : (
            returns.map((r) => (
              <ReturnRow
                key={r.id}
                ret={r}
                selected={selectedId === r.id}
                onSelect={() => setSelectedId(r.id)}
              />
            ))
          )}
        </motion.div>

        {/* Detail pane */}
        <div>
          {selected ? (
            <ReturnDetailPane ret={selected} />
          ) : (
            <EmptyState
              icon={Undo2}
              title="Select a return"
              description="Click a return on the left to review it and take action."
              bordered
            />
          )}
        </div>
      </div>
    </AdminPage>
  );
}
