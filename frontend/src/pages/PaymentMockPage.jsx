import { useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { LockIcon, ShieldIcon, Check, CloseIcon } from '@/components/storefront/Icons';
import { paymentsApi } from '@/features/payments/api.js';
import { formatPrice } from '@/lib/utils.js';

/**
 * Stand-in for PhonePe's hosted page when the active payment gateway is "mock".
 * Two buttons — approve / decline — POST the decision to our webhook,
 * then redirect the user to the `return` URL the backend embedded.
 *
 * Bare route (/payments/mock/:txnId) — no Layout header/footer.
 * Renders its own full-screen wellness canvas.
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
      <main className="paper font-wsans min-h-screen flex items-start justify-center pt-[clamp(40px,8vh,90px)] px-6 pb-16">
        <div className="w-full max-w-[440px] bg-wcard border border-wline rounded-xl3 overflow-hidden shadow-[0_24px_60px_-30px_rgba(40,30,10,0.4)] animate-rise">
          {/* Green header band */}
          <div className="bg-wgreen px-6 py-[18px] flex items-center justify-between">
            <div className="flex items-center gap-2 text-[14px] text-white/90">
              <LockIcon size={16} stroke="#f3efe6" />
              Secure Payment
            </div>
            <span className="text-[12px] text-white/60">Mock Gateway</span>
          </div>

          {/* Error body */}
          <div className="px-6 py-8 text-center">
            <div className="mx-auto mb-4 grid size-12 place-items-center rounded-full bg-amber-50 border border-amber-200">
              {/* Warning triangle — inline SVG, no external icon lib needed */}
              <svg
                width="22"
                height="22"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#92400e"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
                <path d="M12 9v4m0 3h.01" />
              </svg>
            </div>
            <p className="font-semibold text-wink">Missing transaction ID</p>
            <p className="mt-1.5 text-sm text-wmuted">
              This URL is invalid. Please return to the checkout and try again.
            </p>
          </div>
        </div>
      </main>
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
    <main className="paper font-wsans min-h-screen flex items-start justify-center pt-[clamp(40px,8vh,90px)] px-6 pb-16">
      <div className="w-full max-w-[440px] bg-wcard border border-wline rounded-xl3 overflow-hidden shadow-[0_24px_60px_-30px_rgba(40,30,10,0.4)] animate-rise">

        {/* ── Branded green header band ────────────────────────────────── */}
        <div className="bg-wgreen px-6 py-[18px] flex items-center justify-between">
          <div className="flex items-center gap-2 text-[14px] text-white/90">
            <LockIcon size={16} stroke="#f3efe6" />
            Secure Payment
          </div>
          <div className="flex items-center gap-1.5 text-[12px] text-white/60">
            <ShieldIcon size={13} stroke="currentColor" strokeWidth={1.5} />
            Mock Gateway
          </div>
        </div>

        {/* ── Card body ────────────────────────────────────────────────── */}
        <div className="px-6 py-[26px]">

          {/* Amount summary row */}
          <div className="flex justify-between items-center pb-4 border-b border-wline mb-[18px]">
            <div>
              <div className="text-[12px] text-wmuted">Paying to</div>
              <div className="text-[15px] text-wink font-medium">Wellvia Wellness Pvt Ltd</div>
            </div>
            <div className="font-wserif text-[28px] leading-none text-wink">
              {formatPrice(amountMajor)}
            </div>
          </div>

          {/* Transaction ID */}
          <p className="mb-5 text-[11px] text-wmuted break-all">
            Txn&nbsp;
            <code className="font-mono text-[11px] text-wink/70">{txnId}</code>
          </p>

          {/* Eyebrow label */}
          <p className="mb-3 text-[11px] tracking-[0.1em] uppercase text-wmuted">
            Sandbox Mode — no real payment will be taken
          </p>

          {/* ── Action buttons ───────────────────────────────────────── */}
          <div className="grid gap-3 sm:grid-cols-2">
            {/* Approve */}
            <button
              type="button"
              onClick={() => decide('approve')}
              disabled={!!working}
              className="flex items-center justify-center gap-2 w-full bg-wgreen text-white rounded-full py-[14px] text-[15px] font-medium hover:bg-wgreen-dark disabled:opacity-60 transition-colors cursor-pointer"
            >
              {working === 'approve' ? (
                <svg
                  className="animate-spin360 size-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M12 3a9 9 0 1 0 9 9" />
                </svg>
              ) : (
                <Check size={16} />
              )}
              Approve payment
            </button>

            {/* Decline */}
            <button
              type="button"
              onClick={() => decide('decline')}
              disabled={!!working}
              className="flex items-center justify-center gap-2 w-full bg-transparent text-red-600 border border-red-200 rounded-full py-[14px] text-[15px] font-medium hover:bg-red-50 hover:border-red-300 disabled:opacity-60 transition-colors cursor-pointer"
            >
              {working === 'decline' ? (
                <svg
                  className="animate-spin360 size-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M12 3a9 9 0 1 0 9 9" />
                </svg>
              ) : (
                <CloseIcon size={16} />
              )}
              Decline
            </button>
          </div>

          {/* ── Error state ──────────────────────────────────────────── */}
          {error && (
            <div className="mt-4 flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-sm text-red-700">
              <svg
                className="mt-0.5 size-4 shrink-0"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
                <path d="M12 9v4m0 3h.01" />
              </svg>
              <span>{error}</span>
            </div>
          )}

          {/* ── Sandbox disclaimer ───────────────────────────────────── */}
          <p className="mt-6 text-center text-[11.5px] text-wmuted">
            256-bit SSL encrypted · This page only exists while the payment
            gateway is set to{' '}
            <code className="font-mono text-[11px]">Mock</code>. With real
            PhonePe credentials, you&apos;d be on phonepe.com instead.
          </p>
        </div>
      </div>
    </main>
  );
}
