import { motion } from 'framer-motion';
import { MapPin, Briefcase, ArrowUpRight } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS, resolvePageIcon } from '@/features/site-pages/defaults.js';
import {
  ContentPage,
  WProse,
  WSection,
  WSectionLabel,
} from '@/components/storefront/ContentPage.jsx';
import { staggerContainer, fadeUp, listStagger } from '@/lib/motion.js';

// ── Opening card ─────────────────────────────────────────────────────────────

function Opening({ job }) {
  const inner = (
    <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <h3 className="font-semibold text-wink">{job.title}</h3>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-wmuted">
          {job.department && (
            <span className="inline-flex items-center gap-1">
              <Briefcase className="size-3.5 text-wmuted/60" aria-hidden="true" />
              {job.department}
            </span>
          )}
          {job.location && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="size-3.5 text-wmuted/60" aria-hidden="true" />
              {job.location}
            </span>
          )}
          {job.type && (
            <span className="rounded-full border border-wline bg-wcanvas px-2 py-0.5 text-[11px] font-medium text-wmuted">
              {job.type}
            </span>
          )}
        </div>
      </div>
      {job.url && (
        <span className="inline-flex shrink-0 items-center gap-1.5 text-sm font-semibold text-wgold">
          Apply
          <ArrowUpRight className="size-4" aria-hidden="true" />
        </span>
      )}
    </div>
  );

  return (
    <div className="rounded-xl2 border border-wline bg-wcard shadow-sm transition-shadow hover:shadow-md">
      {job.url ? (
        <a
          href={safeUrl(job.url)}
          className="block rounded-xl2 outline-none focus-visible:ring-2 focus-visible:ring-wgold/50"
        >
          {inner}
        </a>
      ) : (
        inner
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function CareersPage() {
  // ── Data wiring (unchanged) ──────────────────────────────────────────────
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.careers, ...data?.careers };

  if (page.enabled === false) {
    return <ContentPage disabled disabledTitle="Careers" />;
  }

  return (
    <ContentPage
      eyebrow={page.hero?.eyebrow}
      title={page.hero?.title}
      subtitle={page.hero?.subtitle}
      extra={
        page.intro ? (
          <p className="text-sm leading-relaxed text-wmuted">{page.intro}</p>
        ) : undefined
      }
    >
      {/* ── Perks ─────────────────────────────────────────────────────── */}
      {page.perks?.length > 0 && (
        <WSection className="mt-0">
          <WSectionLabel className="mb-5">Why you&apos;ll love it here</WSectionLabel>
          <motion.div
            variants={staggerContainer(0.06)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-60px' }}
            className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4"
          >
            {page.perks.map((p, i) => {
              const Icon = resolvePageIcon(p.icon);
              return (
                <motion.div key={i} variants={fadeUp}>
                  <div className="h-full rounded-xl2 border border-wline bg-wcard p-5 shadow-sm">
                    <span className="grid size-10 place-items-center rounded-full bg-wgold/10 text-wgold">
                      <Icon className="size-5" aria-hidden="true" />
                    </span>
                    <h3 className="mt-4 font-semibold text-wink">{p.title}</h3>
                    <p className="mt-1 text-sm leading-relaxed text-wmuted">{p.text}</p>
                  </div>
                </motion.div>
              );
            })}
          </motion.div>
        </WSection>
      )}

      {/* ── Open roles ────────────────────────────────────────────────── */}
      <WSection>
        <WSectionLabel className="mb-5">Open roles</WSectionLabel>
        {page.openings?.length > 0 ? (
          <motion.div
            variants={listStagger(0.04)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-40px' }}
            className="flex flex-col gap-3"
          >
            {page.openings.map((job, i) => (
              <motion.div key={i} variants={fadeUp}>
                <Opening job={job} />
              </motion.div>
            ))}
          </motion.div>
        ) : (
          <div className="flex flex-col items-center gap-3 rounded-xl2 border border-wline bg-wcard py-12 text-center shadow-sm">
            <span className="grid size-12 place-items-center rounded-full bg-wgold/10 text-wgold">
              <Briefcase className="size-6" aria-hidden="true" />
            </span>
            <p className="font-wserif text-lg text-wink">No open roles right now</p>
            <p className="max-w-xs text-sm text-wmuted">
              We&apos;re always glad to hear from great people. Check back soon.
            </p>
          </div>
        )}
      </WSection>

      {/* ── Culture ───────────────────────────────────────────────────── */}
      {page.culture?.heading && (
        <WSection>
          <div className="rounded-xl2 border border-wline bg-wcard p-6 shadow-sm sm:p-8">
            <WSectionLabel className="mb-5">{page.culture.heading}</WSectionLabel>
            <div className="max-w-3xl">
              <WProse text={page.culture.body} />
            </div>
          </div>
        </WSection>
      )}
    </ContentPage>
  );
}
