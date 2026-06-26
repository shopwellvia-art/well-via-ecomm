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
import AccountLayout from '@/components/storefront/AccountLayout';
import { cn, formatPrice } from '@/lib/utils.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useMyLoyalty, useRedeemTier } from '@/features/loyalty/hooks.js';
import { ReferralSection } from '@/features/loyalty/ReferralSection.jsx';
import { VipTierCard } from '@/features/loyalty/VipTierCard.jsx';

/* ── reason label map (unchanged) ───────────────────────────────────── */
const REASON_LABELS = {
  signup_bonus:  { label: 'Welcome bonus',    icon: Sparkles     },
  place_order:   { label: 'Order reward',     icon: ShoppingBag  },
  write_review:  { label: 'Review reward',    icon: Star         },
  redeem:        { label: 'Redeemed',         icon: TicketPercent },
  refund_reversal:{ label: 'Refund reversal', icon: RefreshCw    },
  expiry:        { label: 'Expired',          icon: RefreshCw    },
  admin_adjust:  { label: 'Admin adjustment', icon: RefreshCw    },
};

function formatReason(reason) {
  return REASON_LABELS[reason] || { label: reason.replace(/_/g, ' '), icon: RefreshCw };
}

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
  });
}

function describeTierReward(tier) {
  if (tier.discount_type === 'percent') {
    const cap = tier.max_discount ? ` (up to ${formatPrice(tier.max_discount)})` : '';
    return `${Number(tier.discount_value)}% off${cap}`;
  }
  return `${formatPrice(tier.discount_value)} off`;
}

/* ── inline sub-components ──────────────────────────────────────────── */

function RedeemSuccessBanner({ result, onDismiss }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(result.coupon_code).then(
      () => { setCopied(true); setTimeout(() => setCopied(false), 2000); },
      () => {},
    );
  }
  return (
    <div className="rounded-xl2 border border-wgold/30 bg-wgold/10 p-5">
      <div className="flex items-start gap-3">
        <span className="w-9 h-9 shrink-0 flex items-center justify-center rounded-full bg-wgold/15 text-wgold">
          <Gift size={18} aria-hidden="true" />
        </span>

        <div className="min-w-0 flex-1">
          <p className="font-wserif text-[19px] text-wink leading-snug">Reward unlocked!</p>
          <p className="mt-0.5 text-xs text-wmuted">
            Paste this code at checkout. New balance:{' '}
            <strong className="text-wink">{result.new_balance.toLocaleString()} pts</strong>
          </p>
          <div className="mt-2 inline-flex items-center gap-2 rounded-xl border border-wline bg-wcard px-3 py-1.5">
            <code className="font-mono text-sm tracking-wider text-wink">
              {result.coupon_code}
            </code>
            <button
              type="button"
              onClick={copy}
              aria-label="Copy coupon code"
              className="w-8 h-8 flex items-center justify-center rounded-lg text-wmuted hover:bg-wcanvas hover:text-wink transition-colors"
            >
              {copied
                ? <Check size={14} className="text-wgreen" aria-hidden="true" />
                : <Copy size={14} aria-hidden="true" />}
            </button>
          </div>
        </div>

        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="w-8 h-8 flex items-center justify-center rounded-lg text-wmuted hover:bg-wcanvas hover:text-wink transition-colors text-lg leading-none"
        >
          &times;
        </button>
      </div>
    </div>
  );
}

function TierCard({ tier, balance, onRedeem, redeeming }) {
  const canRedeem = balance >= tier.cost_points && !redeeming;
  const shortBy   = balance < tier.cost_points ? tier.cost_points - balance : 0;
  const pct       = Math.min(100, Math.round((balance / tier.cost_points) * 100));

  return (
    <div className="bg-wcard border border-wline rounded-xl2 p-5 flex flex-col gap-4 transition-shadow hover:shadow-md">
      {/* Header */}
      <div className="flex items-start gap-3">
        <span className="w-9 h-9 shrink-0 flex items-center justify-center rounded-xl bg-wgold/10 text-wgold">
          <TicketPercent size={18} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-wserif text-[17px] text-wink leading-snug">{tier.name}</p>
          <p className="mt-0.5 text-xs text-wmuted">{describeTierReward(tier)}</p>
        </div>
      </div>

      {/* Progress bar */}
      <div>
        <div className="mb-1.5 flex items-baseline justify-between">
          <span className="text-xs text-wmuted">
            {canRedeem ? 'Ready to redeem' : `${shortBy.toLocaleString()} pts needed`}
          </span>
          <span className="text-xs font-semibold text-wink">
            {tier.cost_points.toLocaleString()}{' '}
            <span className="font-normal text-wmuted">pts</span>
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-wline/60">
          <div
            className="h-full rounded-full bg-wgold transition-[width] duration-500"
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-label={`Progress toward ${tier.name}: ${pct}%`}
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
      </div>

      <p className="text-[11px] text-wmuted">
        Coupon valid {tier.expires_after_days} day
        {tier.expires_after_days === 1 ? '' : 's'} after redemption.
      </p>

      {!canRedeem && shortBy > 0 ? (
        <button
          disabled
          className="inline-flex items-center justify-center gap-1.5 border border-wline text-wmuted rounded-full px-4 py-2 text-sm cursor-not-allowed opacity-70"
        >
          Need <span className="ml-0.5">{shortBy.toLocaleString()}</span> more pts
        </button>
      ) : (
        <button
          onClick={() => onRedeem(tier)}
          disabled={!canRedeem || redeeming}
          className="inline-flex items-center justify-center gap-2 bg-wgreen text-white rounded-full px-4 py-2 text-sm font-medium hover:bg-wgreen-dark transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {redeeming
            ? <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
            : <Gift size={14} aria-hidden="true" />}
          Redeem reward
        </button>
      )}
    </div>
  );
}

function TransactionRow({ tx }) {
  const { label, icon: Icon } = formatReason(tx.reason);
  const isCredit = tx.delta > 0;
  return (
    <li className="flex items-center gap-3 border-t border-wline px-4 py-3 first:border-t-0 hover:bg-wpaper/60 transition-colors">
      <span
        className={cn(
          'w-8 h-8 shrink-0 flex items-center justify-center rounded-full',
          isCredit ? 'bg-wgreen/10 text-wgreen' : 'bg-wcanvas text-wmuted',
        )}
      >
        <Icon size={15} aria-hidden="true" />
      </span>

      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-wink">{label}</p>
        {tx.description && (
          <p className="truncate text-[11px] text-wmuted">{tx.description}</p>
        )}
      </div>

      <p className="text-xs text-wmuted">{formatDate(tx.created_at)}</p>

      <p
        className={cn(
          'min-w-[4rem] text-right text-sm font-semibold',
          isCredit ? 'text-wgreen' : 'text-wmuted',
        )}
      >
        {isCredit ? '+' : ''}
        {tx.delta.toLocaleString()}
      </p>
    </li>
  );
}

/* ── page ───────────────────────────────────────────────────────────── */

export default function RewardsPage() {
  const user = useAuthStore((s) => s.user);
  const { data, isLoading, isError, error, refetch } = useMyLoyalty();
  const redeem = useRedeemTier();
  const [success, setSuccess] = useState(null);

  /* ── sign-in gate ──────────────────────────────────────────────────── */
  const status = error?.response?.status;
  if (!user || status === 401) {
    return (
      <AccountLayout active="rewards">
        <div className="flex flex-col items-center justify-center py-24 gap-5 text-center">
          <div className="w-16 h-16 rounded-full bg-wcanvas flex items-center justify-center">
            <Lock size={28} className="text-wmuted" aria-hidden="true" />
          </div>
          <div>
            <h2 className="font-wserif text-2xl text-wink mb-1">
              Sign in to view your rewards
            </h2>
            <p className="text-wmuted text-sm">
              Your points and coupon history live with your account.
            </p>
          </div>
          <Link
            to="/login?next=/rewards"
            className="bg-wgreen text-white rounded-full px-6 py-2.5 text-sm font-medium hover:bg-wgreen-dark transition-colors"
          >
            Sign in
          </Link>
        </div>
      </AccountLayout>
    );
  }

  /* ── error state ───────────────────────────────────────────────────── */
  if (isError) {
    return (
      <AccountLayout active="rewards">
        <div className="flex flex-col items-center justify-center py-24 gap-5 text-center">
          <div className="w-16 h-16 rounded-full bg-red-50 flex items-center justify-center">
            <AlertTriangle size={28} className="text-red-500" aria-hidden="true" />
          </div>
          <div>
            <h2 className="font-wserif text-2xl text-wink mb-1">
              Couldn't load your rewards
            </h2>
            <p className="text-wmuted text-sm">
              Something went wrong on our end. Please try again.
            </p>
          </div>
          <button
            onClick={() => refetch()}
            className="bg-wgreen text-white rounded-full px-6 py-2.5 text-sm font-medium hover:bg-wgreen-dark transition-colors"
          >
            Retry
          </button>
        </div>
      </AccountLayout>
    );
  }

  /* ── data ──────────────────────────────────────────────────────────── */
  const balance     = data?.balance     ?? 0;
  const lifetime    = data?.lifetime    ?? 0;
  const tiers       = data?.tiers       ?? [];
  const recent      = data?.recent      ?? [];
  const tierProgress = data?.tier_progress;

  function handleRedeem(tier) {
    setSuccess(null);
    redeem.mutate(tier.id, {
      onSuccess: (resp) => setSuccess(resp),
      onError: () => {},
    });
  }

  return (
    <AccountLayout active="rewards">
      {/* Page heading */}
      <div className="mb-6">
        <h1 className="font-wserif text-[clamp(26px,3.2vw,38px)] text-wink leading-tight mb-1">
          Wellvia Rewards
        </h1>
        <p className="text-sm text-wmuted font-light">
          Earn with every ritual. Redeem on what you love.
        </p>
      </div>

      {/* ── loading ─────────────────────────────────────────────────────── */}
      {isLoading ? (
        <div className="flex flex-col gap-4">
          <div className="animate-pulse bg-wgreen/20 rounded-xl3 h-44" />
          <div className="animate-pulse bg-wline/40 rounded-xl2 h-28" />
          <div className="animate-pulse bg-wline/40 rounded-xl2 h-64" />
        </div>
      ) : (
        <div className="flex flex-col gap-7">
          {/* Redeem success banner */}
          {success && (
            <RedeemSuccessBanner result={success} onDismiss={() => setSuccess(null)} />
          )}

          {/* ── Points + tier hero card (bg-wgreen) ─────────────────────── */}
          <div className="bg-wgreen rounded-xl3 p-7 lg:p-9 flex flex-wrap justify-between items-center gap-5 animate-rise">
            <div>
              {/* Eyebrow: current VIP tier name */}
              <div className="text-[11px] tracking-[0.18em] uppercase text-white/60 mb-2">
                {tierProgress?.current?.name || 'Member'}
              </div>

              {/* Big balance number */}
              <div className="font-wserif text-[clamp(44px,5vw,60px)] leading-none text-white">
                {balance.toLocaleString()}
                <span className="text-[20px] text-white/70 ml-2">pts</span>
              </div>

              {/* Secondary stats */}
              <div className="mt-3 flex flex-col gap-1">
                <div className="text-[12.5px] text-white/70">
                  Lifetime earned:{' '}
                  <strong className="text-white">{lifetime.toLocaleString()} pts</strong>
                </div>
                {tierProgress?.next && (tierProgress.points_to_next ?? 0) > 0 && (
                  <div className="text-[12.5px] text-white/70">
                    {tierProgress.points_to_next.toLocaleString()} pts to{' '}
                    {tierProgress.next.name}
                  </div>
                )}
                {balance < 0 && (
                  <div className="flex items-center gap-1.5 text-[12px] text-white/70">
                    <TrendingDown size={12} aria-hidden="true" />
                    Balance is negative — earn points to restore it.
                  </div>
                )}
                {balance > 0 && tiers.length > 0 && (
                  <div className="flex items-center gap-1.5 text-[12px] text-white/80">
                    <TrendingUp size={12} aria-hidden="true" />
                    You have rewards available to redeem.
                  </div>
                )}
              </div>
            </div>

            {/* Tier circle badge (mirrors the reference design) */}
            {tierProgress?.current && (
              <div
                className="w-[110px] h-[110px] rounded-full border-2 border-wgold/60 flex items-center justify-center text-center font-wserif text-[15px] text-wgold shrink-0 select-none"
                aria-hidden="true"
              >
                {tierProgress.current.name}
                <br />
                Tier
              </div>
            )}
          </div>

          {/* ── VIP tier progress card ───────────────────────────────────── */}
          <VipTierCard progress={tierProgress} />

          {/* ── Redemption tiers ─────────────────────────────────────────── */}
          <section>
            <div className="mb-4 flex items-baseline justify-between gap-3">
              <h2 className="font-wserif text-[21px] text-wink">Ways to redeem</h2>
              {tiers.length > 0 && (
                <span className="text-xs text-wmuted">
                  {tiers.length} option{tiers.length === 1 ? '' : 's'}
                </span>
              )}
            </div>

            {tiers.length === 0 ? (
              <p className="text-sm text-wmuted py-4">
                No rewards are available right now. Check back soon.
              </p>
            ) : (
              <div className="grid gap-3.5 sm:grid-cols-2 lg:grid-cols-3">
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
              <p className="mt-3 flex items-center gap-1.5 text-sm text-red-500">
                <AlertTriangle size={16} className="shrink-0" aria-hidden="true" />
                {redeem.error?.response?.data?.error?.message ||
                  'Could not redeem. Please try again.'}
              </p>
            )}
          </section>

          {/* ── Referral ─────────────────────────────────────────────────── */}
          <ReferralSection />

          {/* ── Recent activity ──────────────────────────────────────────── */}
          <section>
            <div className="mb-4 flex items-center justify-between gap-2">
              <h2 className="font-wserif text-[21px] text-wink">Recent activity</h2>
              <div className="flex items-center gap-3 text-xs text-wmuted">
                <span className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-wgreen" aria-hidden="true" />
                  Earned
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full bg-wline" aria-hidden="true" />
                  Spent
                </span>
              </div>
            </div>

            {recent.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-10 gap-3 text-center bg-wcard border border-wline rounded-xl2">
                <Coins size={32} className="text-wmuted" aria-hidden="true" />
                <div>
                  <p className="font-wserif text-lg text-wink mb-0.5">No activity yet</p>
                  <p className="text-sm text-wmuted">
                    Place an order or write a review to start earning points.
                  </p>
                </div>
              </div>
            ) : (
              <div className="overflow-hidden rounded-xl2 border border-wline bg-wcard">
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
    </AccountLayout>
  );
}
