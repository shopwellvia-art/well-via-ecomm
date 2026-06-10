import { useState } from 'react';
import { Copy, Check, Share2, Users, Clock, Gift, ArrowRight } from 'lucide-react';
import { motion } from 'framer-motion';
import { Card } from '@/components/ui/Card.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { cn } from '@/lib/utils.js';
import { fadeUp, listStagger } from '@/lib/motion.js';
import { useMyReferralOverview, useMyReferrals } from './hooks.js';

function formatDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}

function CopyButton({ value, label = 'Copy' }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard.writeText(value).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      },
      () => {},
    );
  }
  return (
    <Button type="button" size="sm" variant={copied ? 'ghost' : 'outline'} onClick={copy}>
      {copied ? (
        <>
          <Check className="size-4 text-success" aria-hidden="true" />
          <span className="text-success">Copied</span>
        </>
      ) : (
        <>
          <Copy className="size-4" aria-hidden="true" />
          {label}
        </>
      )}
    </Button>
  );
}

function NativeShareButton({ url, friendAmount }) {
  // Wrap navigator.share — only render if the device supports it; the desktop
  // Copy button is the fallback path.
  if (typeof navigator === 'undefined' || !navigator.share) return null;
  function share() {
    navigator
      .share({
        title: 'Try this store',
        text: `Use my link and get ₹${friendAmount} off your first order.`,
        url,
      })
      .catch(() => {});
  }
  return (
    <Button type="button" size="sm" variant="outline" onClick={share}>
      <Share2 className="size-4" aria-hidden="true" />
      Share
    </Button>
  );
}

export function ReferralSection() {
  const { data, isLoading } = useMyReferralOverview();
  const { data: list, isLoading: listLoading } = useMyReferrals({
    page: 1,
    page_size: 10,
  });

  if (isLoading) {
    return (
      <section>
        <Skeleton variant="text" className="h-6 w-32 mb-4" />
        <Skeleton className="h-64 rounded-xl" />
      </section>
    );
  }
  if (!data) return null;

  const items = list?.items || [];

  return (
    <section>
      <h2 className="text-h3 text-ink-primary tracking-tight">Refer a friend</h2>

      <motion.div
        variants={fadeUp}
        initial="hidden"
        animate="show"
      >
        <Card className="mt-4 overflow-hidden p-0">
          {/* Reward explanation header */}
          <div className="border-b border-line-subtle px-6 py-4">
            <div className="flex items-start gap-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-accent/12 text-accent">
                <Gift className="size-5" aria-hidden="true" />
              </span>
              <p className="text-sm text-ink-secondary leading-relaxed">
                Share your link. Your friend gets{' '}
                <strong className="text-ink-primary nums">
                  ₹{data.friend_welcome_amount}
                </strong>{' '}
                off their first order (min ₹
                <span className="nums">{data.friend_welcome_min_order}</span>). After
                they complete it, you earn{' '}
                <strong className="text-ink-primary nums">
                  ₹{data.referrer_reward_amount}
                </strong>{' '}
                off (min ₹<span className="nums">{data.referrer_reward_min_order}</span>
                ).
              </p>
            </div>
          </div>

          <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_1px_240px]">
            {/* Code + link section */}
            <div className="flex flex-col gap-5 px-6 py-5">
              {/* Referral code */}
              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
                  Your referral code
                </label>
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex min-w-0 flex-1 items-center gap-2.5 rounded-xl border border-line-subtle bg-bg-sunken px-4 py-2.5">
                    <code className="flex-1 truncate font-mono text-base font-semibold tracking-[0.12em] text-ink-primary nums">
                      {data.code}
                    </code>
                  </div>
                  <CopyButton value={data.code} />
                </div>
              </div>

              {/* Share link */}
              <div>
                <label className="block text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary">
                  Share link
                </label>
                <div className="mt-2 flex items-center gap-2">
                  <div className="flex min-w-0 flex-1 items-center rounded-xl border border-line-subtle bg-bg-sunken px-3.5 py-2.5 overflow-hidden">
                    <ArrowRight className="mr-2 size-3.5 shrink-0 text-ink-tertiary" aria-hidden="true" />
                    <code className="truncate font-mono text-xs text-ink-secondary">
                      {data.share_url}
                    </code>
                  </div>
                  <CopyButton value={data.share_url} label="Copy link" />
                  <NativeShareButton
                    url={data.share_url}
                    friendAmount={data.friend_welcome_amount}
                  />
                </div>
              </div>
            </div>

            {/* Divider */}
            <div className="hidden bg-line-subtle md:block" />

            {/* Stats sidebar */}
            <div className="flex flex-col gap-2.5 border-t border-line-subtle bg-bg-sunken px-5 py-5 md:border-t-0">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-ink-tertiary mb-1">
                Your referrals
              </p>
              <StatCard
                label="Total invited"
                value={data.total}
                icon={Users}
                tone="default"
              />
              <StatCard
                label="Completed"
                value={data.completed}
                icon={Check}
                tone="success"
              />
              <StatCard
                label="Pending"
                value={data.pending}
                icon={Clock}
                tone="muted"
              />
            </div>
          </div>
        </Card>
      </motion.div>

      {/* Referral list */}
      {items.length > 0 && (
        <motion.div
          variants={listStagger(0.04)}
          initial="hidden"
          animate="show"
          className="mt-4 overflow-hidden rounded-xl border border-line-subtle bg-bg-elevated"
        >
          <table className="w-full">
            <thead>
              <tr className="border-b border-line-subtle text-left">
                <th className="px-4 py-3 text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
                  Friend
                </th>
                <th className="px-4 py-3 text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
                  Joined
                </th>
                <th className="hidden px-4 py-3 text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary sm:table-cell">
                  Completed
                </th>
                <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-ink-tertiary">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr
                  key={r.id}
                  className="border-t border-line-subtle transition-colors hover:bg-bg-sunken/60"
                >
                  <td className="px-4 py-3 text-sm text-ink-primary">
                    {r.referred_display}
                  </td>
                  <td className="px-4 py-3 text-xs text-ink-tertiary nums">
                    {formatDate(r.created_at)}
                  </td>
                  <td className="hidden px-4 py-3 text-xs text-ink-tertiary sm:table-cell nums">
                    {formatDate(r.completed_at) || '—'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Badge
                      tone={r.status === 'completed' ? 'success' : 'neutral'}
                      size="md"
                      className="capitalize"
                    >
                      {r.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </motion.div>
      )}
    </section>
  );
}

function StatCard({ label, value, icon: Icon, tone }) {
  return (
    <div
      className={cn(
        'flex items-center gap-3 rounded-lg border border-line-subtle bg-bg-elevated px-3.5 py-2.5 transition-colors',
      )}
    >
      <span
        className={cn(
          'grid size-7 shrink-0 place-items-center rounded-lg',
          tone === 'success' ? 'bg-success/12 text-success' : 'bg-fill text-ink-tertiary',
        )}
      >
        <Icon className="size-3.5" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[10px] uppercase tracking-wide text-ink-tertiary">{label}</p>
        <p className="text-lg font-semibold text-ink-primary nums leading-tight">
          {value.toLocaleString()}
        </p>
      </div>
    </div>
  );
}
