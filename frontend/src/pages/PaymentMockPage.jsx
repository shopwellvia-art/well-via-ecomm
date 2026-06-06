import { useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { motion } from 'framer-motion';
import { ShieldCheck, X, Check } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { paymentsApi } from '@/features/payments/api.js';
import { formatPrice } from '@/lib/utils.js';
import { fadeUp } from '@/lib/motion.js';

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
      <motion.div
        variants={fadeUp}
        initial="hidden"
        animate="show"
        className="mx-auto max-w-md"
      >
        <Card className="overflow-hidden">
          {/* Branded strip — visually communicates "you are off-site now". */}
          <div className="flex items-center gap-3 bg-[#5f259f] px-6 py-5 text-white">
            <span className="grid size-9 place-items-center rounded-sm bg-white/15">
              <ShieldCheck className="size-5" aria-hidden="true" />
            </span>
            <div>
              <p className="text-sm font-semibold">PhonePe (Sandbox simulator)</p>
              <p className="text-xs opacity-80">Demo mode — no real payment will be taken.</p>
            </div>
          </div>

          <div className="px-6 py-7">
            <p className="text-xs uppercase tracking-wide text-ink-tertiary">Amount to pay</p>
            <p className="mt-1 text-h1 text-ink-primary">{formatPrice(amountMajor)}</p>
            <p className="mt-2 break-all text-xs text-ink-secondary">
              Txn: <code>{txnId}</code>
            </p>

            <div className="mt-6 grid gap-3 sm:grid-cols-2">
              <Button
                variant="primary"
                size="lg"
                onClick={() => decide('approve')}
                loading={working === 'approve'}
                disabled={!!working}
              >
                <Check className="size-4" aria-hidden="true" />
                Approve payment
              </Button>
              <Button
                variant="secondary"
                size="lg"
                onClick={() => decide('decline')}
                loading={working === 'decline'}
                disabled={!!working}
              >
                <X className="size-4" aria-hidden="true" />
                Decline
              </Button>
            </div>

            {error && (
              <p className="mt-4 text-sm text-danger">{error}</p>
            )}

            <p className="mt-6 text-center text-xs text-ink-tertiary">
              This page only exists while the payment gateway is set to <code>Mock</code>.
              With real PhonePe creds, you&apos;d be on phonepe.com instead.
            </p>
          </div>
        </Card>
      </motion.div>
    </Page>
  );
}
