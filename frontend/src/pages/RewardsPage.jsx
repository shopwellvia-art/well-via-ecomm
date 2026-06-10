import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
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
import { Card } from '@/components/ui/Card.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { cn, formatPrice } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useMyLoyalty, useRedeemTier } from '@/features/loyalty/hooks.js';
import { ReferralSection } from '@/features/loyalty/ReferralSection.jsx';
import { VipTierCard } from '@/features/loyalty/VipTierCard.jsx';
import { fadeUp, staggerContainer, listStagger } from '@/lib/motion.js';

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
    <motion.div
      variants={fadeUp}
      initial="hidden"
      animate="show"
      className="rounded-xl border border-success/25 bg-success/8 p-5 shadow-glow-success"
    >
      <div className="flex items-start gap-4">
        <span className="grid size-11 shrink-0 place-items-center rounded-full bg-success/12 text-success">
          <Gift className="size-6" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold text-ink-primary">Reward unlocked!</h3>
          <p className="mt-0.5 text-sm text-ink-secondary">
            Paste this code at checkout. New balance:{' '}
            <strong className="text-ink-primary nums">
              {result.new_balance.toLocaleString()} pts
            </strong>
          </p>
          <div className="mt-3 inline-flex items-center gap-2 rounded-lg border border-line-subtle bg-bg-elevated px-3 py-2 shadow-sm">
            <code className="font-mono text-sm tracking-wider text-ink-primary">
              {result.coupon_code}
            </code>
            <button
              type="button"
              onClick={copy}
              aria-label="Copy coupon code"
              className="grid size-7 place-items-center rounded-md text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              {copied ? (
                <Check className="size-4 text-success" />
              ) : (
                <Copy className="size-4" />
              )}
            </button>
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="grid size-8 place-items-center rounded-lg text-ink-tertiary hover:bg-fill hover:text-ink-primary transition-colors focus-visible:focus-ring"
        >
          ×
        </button>
      </div>
    </motion.div>
  );
}

function TierCard({ tier, balance, onRedeem, redeeming }) {
  const canRedeem = balance >= tier.cost_points && !redeeming;
  const shortBy = balance < tier.cost_points ? tier.cost_points - balance : 0;
  const pct = Math.min(100, Math.round((balance / tier.cost_points) * 100));

  return (
    <Card interactive className="flex flex-col gap-4 p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent/12 text-accent">
          <TicketPercent className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink-primary">{tier.name}</p>
          <p className="mt-0.5 text-xs text-ink-secondary">{describeTierReward(tier)}</p>
        </div>
      </div>

      {/* Progress to this tier */}
      <div>
        <div className="mb-1.5 flex items-baseline justify-between">
          <span className="text-xs text-ink-tertiary">
            {canRedeem ? 'Ready to redeem' : `${shortBy.toLocaleString()} pts needed`}
          </span>
          <span className="text-xs font-semibold text-ink-primary nums">
            {tier.cost_points.toLocaleString()}{' '}
            <span className="font-normal text-ink-tertiary">pts</span>
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-fill">
          <div
            className="h-full rounded-full bg-gradient-to-r from-accent to-accent-mid transition-[width] duration-500"
            style={{ width: `${pct}%` }}
            role="progressbar"
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
        <Button size="sm" disabled>
          Need <span className="nums">{shortBy.toLocaleString()}</span> more pts
        </Button>
      ) : (
        <Button
          size="sm"
          onClick={() => onRedeem(tier)}
          loading={redeeming}
          disabled={!canRedeem}
          className={canRedeem ? 'accent-halo' : ''}
        >
          <Gift className="size-4" aria-hidden="true" />
          Redeem reward
        </Button>
      )}
    </Card>
  );
}

function TransactionRow({ tx }) {
  const { label, icon: Icon } = formatReason(tx.reason);
  const isCredit = tx.delta > 0;
  return (
    <li className="flex items-center gap-3 border-t border-line-subtle px-4 py-3 first:border-t-0 hover:bg-bg-sunken/50 transition-colors">
      <span
        className={cn(
          'grid size-8 shrink-0 place-items-center rounded-full transition-colors',
          isCredit ? 'bg-success/12 text-success' : 'bg-fill text-ink-secondary',
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
          'min-w-[4rem] text-right text-sm font-semibold nums',
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
        <h1 className="text-h1 text-ink-primary tracking-tight">Rewards</h1>
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
        <h1 className="text-h1 text-ink-primary tracking-tight">Rewards</h1>
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
      <Breadcrumbs current="Rewards" className="mb-5" />

      <h1 className="text-h1 text-ink-primary tracking-tight">Rewards</h1>
      <p className="mt-1 text-sm text-ink-secondary">
        Earn points on every order. Redeem for discounts.
      </p>

      {isLoading ? (
        <div className="mt-8 flex flex-col gap-6">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-32 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      ) : (
        <motion.div
          variants={staggerContainer(0.08)}
          initial="hidden"
          animate="show"
          className="mt-8 flex flex-col gap-8"
        >
          {success && (
            <RedeemSuccessBanner result={success} onDismiss={() => setSuccess(null)} />
          )}

          {/* VIP Tier */}
          <motion.div variants={fadeUp}>
            <VipTierCard progress={tierProgress} />
          </motion.div>

          {/* Balance card */}
          <motion.div variants={fadeUp}>
            <Card className="overflow-hidden p-0">
              <div className="h-1 w-full bg-gradient-to-r from-accent via-accent-mid to-accent/60" aria-hidden="true" />
              <div className="grid gap-0 sm:grid-cols-[1fr_1px_1fr]">
                <div className="p-6">
                  <p className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
                    <Coins className="size-3.5" aria-hidden="true" />
                    Available balance
                  </p>
                  <p className="mt-3 text-[2.5rem] font-semibold leading-none text-ink-primary nums">
                    {balance.toLocaleString()}
                    <span className="ml-2 text-base font-normal text-ink-tertiary">pts</span>
                  </p>
                  {balance < 0 && (
                    <p className="mt-2 flex items-center gap-1 text-xs text-warning">
                      <TrendingDown className="size-3" aria-hidden="true" />
                      Balance is negative — earn points to restore it.
                    </p>
                  )}
                  {balance > 0 && tiers.length > 0 && (
                    <p className="mt-2 flex items-center gap-1 text-xs text-success">
                      <TrendingUp className="size-3" aria-hidden="true" />
                      You have rewards available to redeem.
                    </p>
                  )}
                </div>

                <div className="hidden bg-line-subtle sm:block" />

                <div className="border-t border-line-subtle p-6 sm:border-t-0">
                  <p className="text-xs font-semibold uppercase tracking-widest text-ink-tertiary">
                    Lifetime earned
                  </p>
                  <p className="mt-3 text-[1.75rem] font-semibold leading-none text-ink-secondary nums">
                    {lifetime.toLocaleString()}
                    <span className="ml-2 text-sm font-normal text-ink-tertiary">pts</span>
                  </p>
                  <p className="mt-2 text-xs text-ink-tertiary">
                    Total points you've ever earned.
                  </p>
                </div>
              </div>
            </Card>
          </motion.div>

          {/* Redemption tiers */}
          <motion.section variants={fadeUp}>
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="text-h3 text-ink-primary tracking-tight">Ways to redeem</h2>
              {tiers.length > 0 && (
                <p className="text-xs text-ink-tertiary nums">{tiers.length} option{tiers.length === 1 ? '' : 's'}</p>
              )}
            </div>
            {tiers.length === 0 ? (
              <p className="mt-3 text-sm text-ink-secondary">
                No rewards are available right now. Check back soon.
              </p>
            ) : (
              <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
          </motion.section>

          {/* Referral */}
          <motion.div variants={fadeUp}>
            <ReferralSection />
          </motion.div>

          {/* Recent activity */}
          <motion.section variants={fadeUp}>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-h3 text-ink-primary tracking-tight">Recent activity</h2>
              <div className="flex items-center gap-3 text-xs text-ink-tertiary">
                <span className="flex items-center gap-1">
                  <span className="size-1.5 rounded-full bg-success" aria-hidden="true" />
                  Earned
                </span>
                <span className="flex items-center gap-1">
                  <span className="size-1.5 rounded-full bg-fill-strong" aria-hidden="true" />
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
              <motion.ul
                variants={listStagger(0.04)}
                initial="hidden"
                animate="show"
                className="mt-4 overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated"
              >
                {recent.map((tx) => (
                  <TransactionRow key={tx.id} tx={tx} />
                ))}
              </motion.ul>
            )}
          </motion.section>
        </motion.div>
      )}
    </Page>
  );
}
