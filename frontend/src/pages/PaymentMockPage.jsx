import { useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { ShieldCheck, X, Check, Lock, AlertTriangle } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { paymentsApi } from '@/features/payments/api.js';
import { formatPrice } from '@/lib/utils.js';

/**
 * Stand-in for PhonePe's hosted page when the active payment gateway is "mock".
 * Two buttons — approve / decline — POST the decision to our webhook,
 * then redirect the user to the `return` URL the backend embedded.
 */
export default function PaymentMockPage() {
  const { txnId } = useParams();
  const [searchParams] = useSearchParams();
  const returnUrl = searchParams.get('return');
  // Amount comes through as paise/cents (smallest unit) — convert for display.
  const amountMinor = Number(searchParams.get('amount') || 0);
  const amountMajor = useMemo(() => amountMinor / 100, [amountMinor]);

  const [working, setWorking] = useState(null); // 'approve' | 'decline' | null
  const [error, setError] = useState(null);

  // Only allow site-relative return URLs (must start with "/") to prevent
  // open-redirect attacks where an attacker crafts a `return` param pointing
  // to an external site.  Mirrors the LoginPage pattern (~line 196).
  const safeReturnUrl =
    returnUrl && returnUrl.startsWith('/')
      ? returnUrl
      : `/payments/return?mtid=${txnId}`;

  // Guard: if the route was reached without a :txnId segment (stale bookmark,
  // direct URL entry), render a clear error rather than silently sending
  // `merchant_transaction_id: undefined` to the API.
  // All hooks are declared above so React's rules-of-hooks are satisfied.
  if (!txnId) {
    return (
      <Page>
        <div className="flex min-h-[70vh] items-center justify-center py-12">
          <Card className="p-8 text-center">
            <AlertTriangle className="mx-auto mb-3 size-8 text-warning" aria-hidden="true" />
            <p className="font-semibold text-ink-primary">Missing transaction ID</p>
            <p className="mt-1 text-sm text-ink-secondary">
              This URL is invalid. Please return to the checkout and try again.
            </p>
          </Card>
        </div>
      </Page>
    );
  }

  async function decide(action) {
    setWorking(action);
    setError(null);
    try {
      await paymentsApi.mockDecision(txnId, action);
      // After the order state is settled we send the user back to the return
      // URL, mirroring what PhonePe's hosted page does in production.
      window.location.assign(safeReturnUrl);
    } catch (err) {
      setWorking(null);
      setError(
        err?.response?.data?.error?.message ||
          'Could not record your decision. Try again.',
      );
    }
  }

  return (
    <Page>
      <div className="flex min-h-[70vh] items-center justify-center py-12">
        <div className="w-full max-w-md">
          <Card className="overflow-hidden p-0">
            {/* Branded header — PhonePe purple, flat (no gradient).
                bg-[#5f259f] is an intentional brand-color bypass: this purple
                is PhonePe's registered brand color and has no semantic token
                equivalent. It is scoped to this dev-only sandbox simulator. */}
            <div className="flex items-center gap-3 bg-[#5f259f] px-6 py-5 text-white">
              <span className="grid size-10 shrink-0 place-items-center rounded-sm bg-white/12">
                <ShieldCheck className="size-5" aria-hidden="true" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold leading-tight">
                  PhonePe (Sandbox simulator)
                </p>
                <p className="mt-0.5 text-xs opacity-70">
                  Demo mode — no real payment will be taken.
                </p>
              </div>
              <Lock className="size-4 shrink-0 opacity-60" aria-hidden="true" />
            </div>

            <div className="px-6 py-8">
              {/* Amount display */}
              <div className="rounded-sm border border-line-subtle bg-bg-sunken px-5 py-4">
                <p className="text-[11px] font-bold uppercase tracking-widest text-ink-tertiary">
                  Amount due
                </p>
                <p className="mt-2 break-words text-3xl font-semibold leading-none text-ink-primary nums sm:text-4xl">
                  {formatPrice(amountMajor)}
                </p>
                <p className="mt-2 flex items-center gap-1.5 break-all text-xs text-ink-tertiary">
                  <span className="shrink-0 font-medium">Txn</span>
                  <code className="font-mono text-[11px] text-ink-secondary nums">
                    {txnId}
                  </code>
                </p>
              </div>

              {/* Action buttons */}
              <div className="mt-6 grid gap-3 sm:grid-cols-2">
                <Button
                  variant="primary"
                  size="lg"
                  onClick={() => decide('approve')}
                  loading={working === 'approve'}
                  disabled={!!working}
                  className="gap-2"
                >
                  <Check className="size-4" aria-hidden="true" />
                  Approve payment
                </Button>
                <Button
                  variant="outline"
                  size="lg"
                  onClick={() => decide('decline')}
                  loading={working === 'decline'}
                  disabled={!!working}
                  className="gap-2 border-danger/40 text-danger hover:border-danger/60 hover:bg-danger/8"
                >
                  <X className="size-4" aria-hidden="true" />
                  Decline
                </Button>
              </div>

              {/* Error state */}
              {error && (
                <div className="mt-4 flex items-start gap-2 rounded-sm border border-danger/25 bg-danger/8 px-3.5 py-2.5 text-sm text-danger">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  <span>{error}</span>
                </div>
              )}

              {/* Sandbox disclaimer */}
              <p className="mt-6 text-center text-xs text-ink-tertiary">
                This page only exists while the payment gateway is set to{' '}
                <code className="font-mono text-[11px]">Mock</code>. With real
                PhonePe credentials, you&apos;d be on phonepe.com instead.
              </p>
            </div>
          </Card>
        </div>
      </div>
    </Page>
  );
}
