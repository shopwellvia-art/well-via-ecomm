import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Coins,
  Gift,
  Lock,
  AlertTriangle,
  Check,
  Copy,
  ShoppingBag,
  Star,
  RefreshCw,
  TicketPercent,
  Sparkles,
  TrendingUp,
  TrendingDown,
} from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useMyLoyalty, useRedeemTier } from '@/features/loyalty/hooks.js';
import { ReferralSection } from '@/features/loyalty/ReferralSection.jsx';
import { VipTierCard } from '@/features/loyalty/VipTierCard.jsx';

const REASON_LABELS = {
  signup_bonus: { label: 'Welcome bonus', icon: Sparkles },
  place_order: { label: 'Order reward', icon: ShoppingBag },
  write_review: { label: 'Review reward', icon: Star },
  redeem: { label: 'Redeemed', icon: TicketPercent },
  refund_reversal: { label: 'Refund reversal', icon: RefreshCw },
  expiry: { label: 'Expired', icon: RefreshCw },
  admin_adjust: { label: 'Admin adjustment', icon: RefreshCw },
};

function formatReason(reason) {
  return REASON_LABELS[reason] || { label: reason.replace(/_/g, ' '), icon: RefreshCw };
}

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function describeTierReward(tier) {
  if (tier.discount_type === 'percent') {
    const cap = tier.max_discount ? ` (up to ${formatPrice(tier.max_discount)})` : '';
    return `${Number(tier.discount_value)}% off${cap}`;
  }
  return `${formatPrice(tier.discount_value)} off`;
}

function RedeemSuccessBanner({ result, onDismiss }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(result.coupon_code).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      },
      () => {},
    );
  }
  return (
    <div className="rounded-sm border border-success/30 bg-success/8 p-4 shadow-sm">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-success/12 text-success">
          <Gift className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink-primary">Reward unlocked!</p>
          <p className="mt-0.5 text-xs text-ink-secondary">
            Paste this code at checkout. New balance:{' '}
            <strong className="nums text-ink-primary">
              {result.new_balance.toLocaleString()} pts
            </strong>
          </p>
          <div className="mt-2 inline-flex items-center gap-2 rounded-sm border border-line-subtle bg-bg-elevated px-3 py-1.5 shadow-sm">
            <code className="nums font-mono text-sm tracking-wider text-ink-primary">
              {result.coupon_code}
            </code>
            <button
              type="button"
              onClick={copy}
              aria-label="Copy coupon code"
              className="grid size-9 place-items-center rounded-xs text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              {copied ? (
                <Check className="size-3.5 text-success" />
              ) : (
                <Copy className="size-3.5" />
              )}
            </button>
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="grid size-9 place-items-center rounded-xs text-ink-tertiary hover:bg-fill hover:text-ink-primary transition-colors focus-visible:focus-ring"
        >
          &times;
        </button>
      </div>
    </div>
  );
}

function TierCard({ tier, balance, onRedeem, redeeming }) {
  const canRedeem = balance >= tier.cost_points && !redeeming;
  const shortBy = balance < tier.cost_points ? tier.cost_points - balance : 0;
  const pct = Math.min(100, Math.round((balance / tier.cost_points) * 100));

  return (
    <div className="flex flex-col gap-4 rounded-sm border border-line-subtle bg-bg-elevated p-4 shadow-sm transition-shadow hover:shadow-md">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-sm bg-accent/12 text-accent">
          <TicketPercent className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink-primary">{tier.name}</p>
          <p className="mt-0.5 text-xs text-ink-secondary">{describeTierReward(tier)}</p>
        </div>
      </div>

      {/* Progress */}
      <div>
        <div className="mb-1.5 flex items-baseline justify-between">
          <span className="text-xs text-ink-tertiary">
            {canRedeem ? 'Ready to redeem' : `${shortBy.toLocaleString()} pts needed`}
          </span>
          <span className="nums text-xs font-semibold text-ink-primary">
            {tier.cost_points.toLocaleString()}{' '}
            <span className="font-normal text-ink-tertiary">pts</span>
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-line-subtle">
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-500"
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-label={`Progress toward ${tier.name}: ${pct}%`}
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
      </div>

      <p className="text-[11px] text-ink-tertiary">
        Coupon valid {tier.expires_after_days} day
        {tier.expires_after_days === 1 ? '' : 's'} after redemption.
      </p>

      {!canRedeem && shortBy > 0 ? (
        <Button size="sm" disabled variant="secondary">
          Need <span className="nums ml-1">{shortBy.toLocaleString()}</span> more pts
        </Button>
      ) : (
        <Button
          size="sm"
          onClick={() => onRedeem(tier)}
          loading={redeeming}
          disabled={!canRedeem}
        >
          <Gift className="size-4" aria-hidden="true" />
          Redeem reward
        </Button>
      )}
    </div>
  );
}

function TransactionRow({ tx }) {
  const { label, icon: Icon } = formatReason(tx.reason);
  const isCredit = tx.delta > 0;
  return (
    <li className="flex items-center gap-3 border-t border-line-subtle px-4 py-3 first:border-t-0 hover:bg-bg-sunken transition-colors">
      <span
        className={cn(
          'grid size-8 shrink-0 place-items-center rounded-full',
          isCredit ? 'bg-success/12 text-success' : 'bg-bg-sunken text-ink-secondary',
        )}
      >
        <Icon className="size-4" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-ink-primary">{label}</p>
        {tx.description && (
          <p className="truncate text-[11px] text-ink-tertiary">{tx.description}</p>
        )}
      </div>
      <p className="text-xs text-ink-tertiary">{formatDate(tx.created_at)}</p>
      <p
        className={cn(
          'nums min-w-[4rem] text-right text-sm font-semibold',
          isCredit ? 'text-success' : 'text-ink-secondary',
        )}
      >
        {isCredit ? '+' : ''}
        {tx.delta.toLocaleString()}
      </p>
    </li>
  );
}

export default function RewardsPage() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading, isError, error, refetch } = useMyLoyalty();
  const redeem = useRedeemTier();
  const [success, setSuccess] = useState(null);

  const status = error?.response?.status;
  if (!user || status === 401) {
    return (
      <Page>
        <h1 className="text-lg font-semibold text-ink-primary">Rewards</h1>
        <div className="mt-8">
          <EmptyState
            icon={Lock}
            title="Sign in to view your rewards"
            description="Your points and coupon history live with your account."
            action={
              <Link to="/login?next=/rewards">
                <Button size="sm">Sign in</Button>
              </Link>
            }
          />
        </div>
      </Page>
    );
  }

  if (isError) {
    return (
      <Page>
        <h1 className="text-lg font-semibold text-ink-primary">Rewards</h1>
        <div className="mt-8">
          <EmptyState
            icon={AlertTriangle}
            iconTone="danger"
            title="Couldn't load your rewards"
            description="Something went wrong on our end. Please try again."
            action={
              <Button size="sm" onClick={() => refetch()}>
                Retry
              </Button>
            }
          />
        </div>
      </Page>
    );
  }

  const balance = data?.balance ?? 0;
  const lifetime = data?.lifetime ?? 0;
  const tiers = data?.tiers ?? [];
  const recent = data?.recent ?? [];
  const tierProgress = data?.tier_progress;

  function handleRedeem(tier) {
    setSuccess(null);
    redeem.mutate(tier.id, {
      onSuccess: (resp) => setSuccess(resp),
      onError: () => {},
    });
  }

  return (
    <Page>
      <Breadcrumbs current="Rewards" className="mb-4" />

      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-ink-primary">My Rewards</h1>
        <p className="text-xs text-ink-secondary">
          Earn points on every order
        </p>
      </div>

      {isLoading ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32 rounded-sm" />
          <Skeleton className="h-24 rounded-sm" />
          <Skeleton className="h-64 rounded-sm" />
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          {success && (
            <RedeemSuccessBanner result={success} onDismiss={() => setSuccess(null)} />
          )}

          {/* VIP Tier */}
          <VipTierCard progress={tierProgress} />

          {/* Balance card — Flipkart-style blue-tinted summary */}
          <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
            {/* accent top stripe */}
            <div className="h-1 w-full bg-accent" aria-hidden="true" />
            <div className="grid gap-0 divide-y divide-line-subtle sm:grid-cols-2 sm:divide-x sm:divide-y-0">
              <div className="px-5 py-4">
                <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-tertiary">
                  <Coins className="size-3.5" aria-hidden="true" />
                  Available balance
                </p>
                <p className="nums mt-2 text-3xl font-bold leading-none text-ink-primary">
                  {balance.toLocaleString()}
                  <span className="ml-1.5 text-sm font-normal text-ink-tertiary">pts</span>
                </p>
                {balance < 0 && (
                  <p className="mt-1.5 flex items-center gap-1 text-xs text-warning">
                    <TrendingDown className="size-3" aria-hidden="true" />
                    Balance is negative — earn points to restore it.
                  </p>
                )}
                {balance > 0 && tiers.length > 0 && (
                  <p className="mt-1.5 flex items-center gap-1 text-xs text-success">
                    <TrendingUp className="size-3" aria-hidden="true" />
                    You have rewards available to redeem.
                  </p>
                )}
              </div>

              <div className="px-5 py-4">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-tertiary">
                  Lifetime earned
                </p>
                <p className="nums mt-2 text-2xl font-bold leading-none text-ink-secondary">
                  {lifetime.toLocaleString()}
                  <span className="ml-1.5 text-sm font-normal text-ink-tertiary">pts</span>
                </p>
                <p className="mt-1.5 text-xs text-ink-tertiary">
                  Total points you&apos;ve ever earned.
                </p>
              </div>
            </div>
          </div>

          {/* Redemption tiers */}
          <section>
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h2 className="text-sm font-semibold text-ink-primary">Ways to redeem</h2>
              {tiers.length > 0 && (
                <p className="nums text-xs text-ink-tertiary">
                  {tiers.length} option{tiers.length === 1 ? '' : 's'}
                </p>
              )}
            </div>
            {tiers.length === 0 ? (
              <p className="text-sm text-ink-secondary">
                No rewards are available right now. Check back soon.
              </p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {tiers.map((t) => (
                  <TierCard
                    key={t.id}
                    tier={t}
                    balance={balance}
                    onRedeem={handleRedeem}
                    redeeming={redeem.isPending && redeem.variables === t.id}
                  />
                ))}
              </div>
            )}
            {redeem.isError && (
              <p className="mt-3 flex items-center gap-1.5 text-sm text-danger">
                <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
                {redeem.error?.response?.data?.error?.message || 'Could not redeem. Please try again.'}
              </p>
            )}
          </section>

          {/* Referral */}
          <ReferralSection />

          {/* Recent activity */}
          <section>
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-ink-primary">Recent activity</h2>
              <div className="flex items-center gap-3 text-xs text-ink-secondary">
                <span className="flex items-center gap-1">
                  <span className="size-1.5 rounded-full bg-success" aria-hidden="true" />
                  Earned
                </span>
                <span className="flex items-center gap-1">
                  <span className="size-1.5 rounded-full bg-line-strong" aria-hidden="true" />
                  Spent
                </span>
              </div>
            </div>

            {recent.length === 0 ? (
              <EmptyState
                size="sm"
                bordered={false}
                icon={Coins}
                title="No activity yet"
                description="Place an order or write a review to start earning points."
              />
            ) : (
              <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
                <ul>
                  {recent.map((tx) => (
                    <TransactionRow key={tx.id} tx={tx} />
                  ))}
                </ul>
              </div>
            )}
          </section>
        </div>
      )}
    </Page>
  );
}
