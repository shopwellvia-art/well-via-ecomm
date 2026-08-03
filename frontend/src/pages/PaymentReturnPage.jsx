import { Link, useSearchParams } from 'react-router-dom';
import { usePaymentStatus } from '@/features/payments/hooks.js';
import { formatPrice } from '@/lib/utils.js';
import { Check, CloseIcon } from '@/components/storefront/Icons.jsx';
import Logo from '@/components/storefront/Logo.jsx';

/**
 * Bare full-screen route — no Layout header/footer.
 * Lands here from the provider redirect. We don't trust the URL — we poll
 * /payments/{mtid}/status so the server tells us what actually happened.
 */

const TERMINAL_STATES = new Set(['paid', 'cancelled', 'refunded']);

export default function PaymentReturnPage() {
  const [params] = useSearchParams();
  const mtid = params.get('mtid');

  const { data, isLoading, isError } = usePaymentStatus(mtid, {
    enabled: !!mtid,
    refetchInterval: (query) => {
      // Once the query is in error (401 signed-out, 404 unknown mtid, network
      // down after retries) stop polling — otherwise this loops forever, the
      // user stares at the "Confirming" spinner, and every 401 tick also
      // clears their stored session via the apiClient interceptor.
      if (query.state.status === 'error') return false;
      const status = query.state.data?.order_status;
      return status && TERMINAL_STATES.has(status) ? false : 1500;
    },
  });

  if (!mtid) {
    return (
      <FullScreenWrap>
        <BadParamsCard />
      </FullScreenWrap>
    );
  }

  const status = data?.order_status;
  const isPaid = status === 'paid';
  const isFailed = status === 'cancelled' || status === 'refunded';
  const isPending = !status || status === 'pending';

  return (
    <FullScreenWrap>
      {(isLoading || isPending) && !isError && <PendingCard />}
      {isPaid && <SuccessCard data={data} />}
      {isFailed && <FailedCard />}
      {isError && <ErrorCard />}
    </FullScreenWrap>
  );
}

/* ── Layout wrapper ──────────────────────────────────────────────────────── */

function FullScreenWrap({ children }) {
  return (
    <main className="paper min-h-screen flex flex-col items-center justify-center px-6 py-16">
      <div className="mb-10">
        <Logo size="md" stacked={false} to="/" />
      </div>
      <div className="w-full max-w-[480px]">{children}</div>
    </main>
  );
}

/* ── Shared card shell ───────────────────────────────────────────────────── */

function WCard({ children }) {
  return (
    <div className="bg-wcard border border-wline rounded-xl3 p-9 lg:p-14 text-center animate-rise shadow-md">
      {children}
    </div>
  );
}

/* ── States ──────────────────────────────────────────────────────────────── */

/**
 * PENDING — polls until the server returns a terminal status.
 * Shows a spinning ring + animated dots; warns user not to close the tab.
 */
function PendingCard() {
  return (
    <WCard>
      {/* Spinner ring using animate-spin360 */}
      <span className="mx-auto flex size-[78px] items-center justify-center rounded-full bg-wgreen/10">
        <span
          className="block size-10 rounded-full border-4 border-wgreen/20 border-t-wgreen animate-spin360"
          role="status"
          aria-label="Checking payment status"
        />
      </span>

      <h1 className="mt-5 font-wserif text-[clamp(24px,4vw,34px)] font-medium text-wink leading-tight">
        Confirming your payment
      </h1>
      <p className="mt-3 text-[14.5px] text-wmuted leading-relaxed font-light">
        This usually takes only a moment — please don&apos;t close this tab.
      </p>

      {/* Pulsing dots */}
      <div className="mt-6 flex justify-center gap-2">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="inline-block size-2 rounded-full bg-wgreen/40 animate-pulseRing"
            style={{ animationDelay: `${i * 0.25}s` }}
            aria-hidden="true"
          />
        ))}
      </div>
    </WCard>
  );
}

/**
 * SUCCESS (status === 'paid') — green circle, serif headline, amount summary,
 * Track Order → /orders/:id and Continue Shopping → /products.
 */
function SuccessCard({ data }) {
  const trackTo = data?.order_id ? `/orders/${data.order_id}` : '/orders';

  return (
    <WCard>
      {/* Solid green circle with white check — matches reference */}
      <span className="mx-auto flex size-[78px] items-center justify-center rounded-full bg-wgreen">
        <Check size={38} stroke="#fff" strokeWidth={1.6} />
      </span>

      <h1 className="mt-5 font-wserif text-[clamp(28px,4vw,40px)] font-medium text-wink leading-tight">
        Payment Successful
      </h1>
      <p className="mt-2.5 text-[14.5px] text-wmuted leading-relaxed font-light">
        Your order{' '}
        <strong className="text-wink font-medium">#{data?.order_id}</strong>{' '}
        is confirmed. We&apos;ll email you when it ships.
      </p>

      {/* Amount + method summary box */}
      <div className="mt-6 bg-wpaper border border-wline rounded-xl2 p-[18px] flex justify-between items-center text-left">
        <div>
          <div className="text-[11px] text-wmuted uppercase tracking-widest">
            Amount Paid
          </div>
          <div className="font-wserif text-[22px] text-wink mt-1 leading-none">
            {formatPrice(data?.total_amount)}
          </div>
        </div>
        {data?.payment_method && (
          <div className="text-right">
            <div className="text-[11px] text-wmuted uppercase tracking-widest">
              Method
            </div>
            <div className="text-[14px] text-wink mt-1">{data.payment_method}</div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="mt-7 flex gap-3 justify-center flex-wrap">
        <Link
          to={trackTo}
          className="bg-wgreen text-white rounded-full px-7 py-3.5 text-[13.5px] font-medium no-underline hover:bg-wgreen-dark transition-colors"
        >
          Track Order
        </Link>
        <Link
          to="/products"
          className="bg-transparent border border-wline rounded-full px-7 py-3.5 text-[13.5px] text-wink no-underline hover:border-wgreen hover:text-wgreen transition-colors"
        >
          Continue Shopping
        </Link>
      </div>
    </WCard>
  );
}

/**
 * FAILED (status === 'cancelled' | 'refunded') — red circle with X, explains
 * stock was returned, Back to Cart + Browse Products.
 */
function FailedCard() {
  return (
    <WCard>
      <span className="mx-auto flex size-[78px] items-center justify-center rounded-full bg-danger/10">
        <CloseIcon size={36} stroke="#EF4444" strokeWidth={1.8} />
      </span>

      <h1 className="mt-5 font-wserif text-[clamp(24px,4vw,34px)] font-medium text-wink leading-tight">
        Payment Unsuccessful
      </h1>
      <p className="mt-3 text-[14.5px] text-wmuted leading-relaxed font-light">
        Your order wasn&apos;t placed. Stock has been returned to the catalog —
        feel free to try again.
      </p>

      <div className="mt-7 flex gap-3 justify-center flex-wrap">
        <Link
          to="/cart"
          className="bg-wgreen text-white rounded-full px-7 py-3.5 text-[13.5px] font-medium no-underline hover:bg-wgreen-dark transition-colors"
        >
          Back to Cart
        </Link>
        <Link
          to="/products"
          className="bg-transparent border border-wline rounded-full px-7 py-3.5 text-[13.5px] text-wink no-underline hover:border-wgreen hover:text-wgreen transition-colors"
        >
          Browse Products
        </Link>
      </div>
    </WCard>
  );
}

/**
 * API ERROR — could not fetch status; directs user to check their orders.
 */
function ErrorCard() {
  return (
    <WCard>
      <span className="mx-auto flex size-[78px] items-center justify-center rounded-full bg-wgold/10">
        <CloseIcon size={36} stroke="#B49A63" strokeWidth={1.8} />
      </span>

      <h1 className="mt-5 font-wserif text-[clamp(24px,4vw,34px)] font-medium text-wink leading-tight">
        Couldn&apos;t Confirm
      </h1>
      <p className="mt-3 text-[14.5px] text-wmuted leading-relaxed font-light">
        We had trouble checking the payment status. Check your orders in a
        moment to see if it went through.
      </p>

      <div className="mt-7 flex justify-center">
        <Link
          to="/orders"
          className="bg-wgreen text-white rounded-full px-7 py-3.5 text-[13.5px] font-medium no-underline hover:bg-wgreen-dark transition-colors"
        >
          View My Orders
        </Link>
      </div>
    </WCard>
  );
}

/**
 * BAD PARAMS — no ?mtid= in the URL; user navigated here directly.
 */
function BadParamsCard() {
  return (
    <WCard>
      <span className="mx-auto flex size-[78px] items-center justify-center rounded-full bg-wgold/10">
        <CloseIcon size={36} stroke="#B49A63" strokeWidth={1.8} />
      </span>

      <h1 className="mt-5 font-wserif text-[clamp(24px,4vw,34px)] font-medium text-wink leading-tight">
        Missing Transaction
      </h1>
      <p className="mt-3 text-[14.5px] text-wmuted leading-relaxed font-light">
        It looks like you landed here without a transaction ID. If you just paid,
        please check your orders below.
      </p>

      <div className="mt-7 flex justify-center">
        <Link
          to="/orders"
          className="bg-wgreen text-white rounded-full px-7 py-3.5 text-[13.5px] font-medium no-underline hover:bg-wgreen-dark transition-colors"
        >
          View My Orders
        </Link>
      </div>
    </WCard>
  );
}
