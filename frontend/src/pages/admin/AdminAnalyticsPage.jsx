import { Link } from 'react-router-dom';

import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { MODULES, moduleRoute, defaultView, viewRoute } from '@/features/analytics/registry.js';
import { MODULE_ICONS } from '@/features/analytics/registry.icons.js';
import { VIEW_STATE } from '@/features/analytics/viewState.js';
import { cn } from '@/lib/utils.js';

/**
 * The Analytics landing page — 12 module cards.
 *
 * Each card states how many of its views can actually answer today. That is
 * deliberate: a module showing "3 of 8 ready" sets the right expectation before
 * a click, whereas a bare tile implies everything behind it works and turns a
 * gated view into a small disappointment.
 */

const READY_STATES = new Set([VIEW_STATE.LIVE, VIEW_STATE.PARTIAL]);

function readiness(module) {
  const total = module.views.length;
  const ready = module.views.filter((v) => READY_STATES.has(v.state)).length;
  return { ready, total };
}

function ModuleCard({ module }) {
  const Icon = MODULE_ICONS[module.slug];
  const { ready, total } = readiness(module);
  const first = defaultView(module.slug);
  const to = first ? viewRoute(module.slug, first.slug) : moduleRoute(module.slug);

  return (
    <Link
      to={to}
      className={cn(
        'group block rounded-lg transition-colors duration-150',
        'focus-visible:focus-ring'
      )}
    >
      <Card interactive className="h-full">
        <CardBody className="flex h-full flex-col gap-3 p-5">
          <div className="flex items-start gap-3">
            {Icon ? (
              <span className="grid size-9 shrink-0 place-items-center rounded-md bg-accent/12 text-accent">
                <Icon className="size-4.5" aria-hidden="true" />
              </span>
            ) : null}
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-ink-primary">
                {module.name}
              </h3>
              <p className="mt-0.5 line-clamp-2 text-xs text-ink-tertiary">
                {module.summary}
              </p>
            </div>
          </div>

          <div className="mt-auto flex items-center justify-between pt-1">
            <span className="text-xs text-ink-tertiary">
              {total} view{total === 1 ? '' : 's'}
            </span>
            {/* "ready" means the view can answer from data that exists — not
                that it is finished. Saying 0 of 3 up front is kinder than three
                clicks into three gates. */}
            <Badge
              tone={ready === 0 ? 'neutral' : ready === total ? 'success' : 'warning'}
              size="sm"
            >
              {ready} of {total} ready
            </Badge>
          </div>
        </CardBody>
      </Card>
    </Link>
  );
}

export default function AdminAnalyticsPage() {
  const totals = MODULES.reduce(
    (acc, m) => {
      const { ready, total } = readiness(m);
      return { ready: acc.ready + ready, total: acc.total + total };
    },
    { ready: 0, total: 0 }
  );

  return (
    <AdminPage
      title="Analytics"
      description={`${MODULES.length} modules, ${totals.total} views. ${totals.ready} can answer from data available today; the rest state what they need.`}
    >
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {MODULES.map((module) => (
          <ModuleCard key={module.slug} module={module} />
        ))}
      </div>
    </AdminPage>
  );
}
