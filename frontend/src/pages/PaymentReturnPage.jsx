import { Link, useSearchParams } from 'react-router-dom';
import { CheckCircle2, XCircle, Loader2, ShoppingBag, ListOrdered, ArrowRight } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Card } from '@/components/ui/Card.jsx';
import { usePaymentStatus } from '@/features/payments/hooks.js';
import { formatPrice } from '@/lib/utils.js';
import { cn } from '@/lib/utils.js';

const TERMINAL_STATES = new Set(['paid', 'cancelled', 'refunded']);

/**
 * Lands here from the provider redirect. We don't trust the URL — we poll
 * /payments/{mtid}/status so the server tells us what actually happened.
 */
export default function PaymentReturnPage() {
  const [params] = useSearchParams();
  const mtid = params.get('mtid');

  const { data, isLoading, isError } = usePaymentStatus(mtid, {
    enabled: !!mtid,
    refetchInterval: (query) => {
      const status = query.state.data?.order_status;
      return status && TERMINAL_STATES.has(status) ? false : 1500;
    },
  });

  if (!mtid) {
    return (
      <Page>
        <BadParams />
      </Page>
    );
  }

  const status = data?.order_status;
  const isPaid = status === 'paid';
  const isFailed = status === 'cancelled' || status === 'refunded';
  const isPending = !status || status === 'pending';

  return (
    <Page>
      <div className="flex min-h-[60vh] items-center justify-center py-12">
        <div className="w-full max-w-md">
          {(isLoading || isPending) && !isError && (
            <Card className="p-8 text-center">
              <PendingState />
            </Card>
          )}

          {isPaid && (
            <Card className="p-8 text-center">
              <IconRing tone="success">
                <CheckCircle2 className="size-8" aria-hidden="true" />
              </IconRing>
              <h1 className="mt-5 text-xl font-semibold text-ink-primary">
                Payment confirmed
              </h1>
              <p className="mt-2 text-sm text-ink-secondary">
                Order{' '}
                <code className="font-mono text-xs text-ink-primary">
                  #{data.order_id}
                </code>{' '}
                for{' '}
                <strong className="nums">{formatPrice(data.total_amount, data.currency)}</strong>{' '}
                is confirmed. We&apos;ll email you when it ships.
              </p>
              <div className="mt-7 grid gap-2.5 sm:grid-cols-2">
                <Link to="/orders">
                  <Button block variant="primary" size="md">
                    <ListOrdered className="size-4" aria-hidden="true" />
                    View my orders
                  </Button>
                </Link>
                <Link to="/products">
                  <Button block variant="outline" size="md">
                    <ShoppingBag className="size-4" aria-hidden="true" />
                    Keep shopping
                  </Button>
                </Link>
              </div>
            </Card>
          )}

          {isFailed && (
            <Card className="p-8 text-center">
              <IconRing tone="danger">
                <XCircle className="size-8" aria-hidden="true" />
              </IconRing>
              <h1 className="mt-5 text-xl font-semibold text-ink-primary">
                Payment didn&apos;t go through
              </h1>
              <p className="mt-2 text-sm text-ink-secondary">
                Your order wasn&apos;t placed. Stock has been returned to the catalog —
                feel free to try again.
              </p>
              <div className="mt-7 grid gap-2.5 sm:grid-cols-2">
                <Link to="/cart">
                  <Button block variant="primary" size="md">
                    <ArrowRight className="size-4" aria-hidden="true" />
                    Back to cart
                  </Button>
                </Link>
                <Link to="/products">
                  <Button block variant="outline" size="md">
                    <ShoppingBag className="size-4" aria-hidden="true" />
                    Browse products
                  </Button>
                </Link>
              </div>
            </Card>
          )}

          {isError && (
            <Card className="p-8 text-center">
              <IconRing tone="warning">
                <XCircle className="size-8" aria-hidden="true" />
              </IconRing>
              <h1 className="mt-5 text-xl font-semibold text-ink-primary">
                Couldn&apos;t confirm
              </h1>
              <p className="mt-2 text-sm text-ink-secondary">
                We had trouble checking the payment status. Check your orders in a
                moment to see if it went through.
              </p>
              <Link to="/orders" className="mt-7 inline-block">
                <Button variant="primary" size="md">
                  <ListOrdered className="size-4" aria-hidden="true" />
                  View my orders
                </Button>
              </Link>
            </Card>
          )}
        </div>
      </div>
    </Page>
  );
}

function PendingState() {
  return (
    <>
      <span className="mx-auto grid size-14 place-items-center rounded-full bg-accent/12 text-accent">
        <Loader2 className="size-7 animate-spin" aria-hidden="true" />
      </span>
      <h1 className="mt-5 text-xl font-semibold text-ink-primary">
        Confirming your payment
      </h1>
      <p className="mt-2 text-sm text-ink-secondary">
        This usually takes only a moment — please don&apos;t close this tab.
      </p>
      <div className="mt-6 flex justify-center gap-1.5">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="inline-block size-2 rounded-full bg-accent/40 animate-pulseRing"
            style={{ animationDelay: `${i * 0.2}s` }}
            aria-hidden="true"
          />
        ))}
      </div>
    </>
  );
}

function IconRing({ tone, children }) {
  const cls = cn(
    'mx-auto grid size-16 place-items-center rounded-full',
    tone === 'success' && 'bg-success/12 text-success',
    tone === 'danger' && 'bg-danger/12 text-danger',
    tone === 'warning' && 'bg-warning/12 text-warning',
  );
  return <span className={cls}>{children}</span>;
}

function BadParams() {
  return (
    <div className="flex min-h-[50vh] items-center justify-center py-12">
      <Card className="mx-auto max-w-sm p-8 text-center">
        <IconRing tone="warning">
          <XCircle className="size-8" aria-hidden="true" />
        </IconRing>
        <h1 className="mt-5 text-xl font-semibold text-ink-primary">
          Missing transaction reference
        </h1>
        <p className="mt-2 text-sm text-ink-secondary">
          It looks like you landed here without a transaction ID.
        </p>
        <Link to="/orders" className="mt-6 inline-block">
          <Button variant="primary" size="md">
            <ListOrdered className="size-4" aria-hidden="true" />
            View my orders
          </Button>
        </Link>
      </Card>
    </div>
  );
}
