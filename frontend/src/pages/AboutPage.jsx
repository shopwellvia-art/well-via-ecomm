import { motion } from 'framer-motion';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS, resolvePageIcon } from '@/features/site-pages/defaults.js';
import {
  ContentPage,
  WProse,
  WSection,
  WSectionLabel,
} from '@/components/storefront/ContentPage.jsx';
import { staggerContainer, fadeUp } from '@/lib/motion.js';

export default function AboutPage() {
  // ── Data wiring (unchanged) ──────────────────────────────────────────────
  const { data, isError } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.about, ...data?.about };

  if (page.enabled === false) {
    return <ContentPage disabled disabledTitle="About Us" />;
  }

  return (
    <ContentPage
      eyebrow={page.hero?.eyebrow}
      title={page.hero?.title}
      subtitle={page.hero?.subtitle}
    >
      {/* Error banner — cached defaults shown when network failed */}
      {isError && (
        <p role="alert" className="mb-6 text-xs text-red-500">
          Content could not be refreshed. Showing cached defaults.
        </p>
      )}

      {/* ── Stats row ─────────────────────────────────────────────────── */}
      {page.stats?.length > 0 && (
        <WSection className="mt-0">
          <motion.div
            variants={staggerContainer(0.07)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-60px' }}
            className="grid grid-cols-2 gap-4 sm:grid-cols-4"
          >
            {page.stats.map((s) => (
              <motion.div key={s.label} variants={fadeUp}>
                <div className="rounded-xl2 border border-wline bg-wcard px-4 py-6 text-center shadow-sm">
                  <p className="break-words text-2xl font-bold text-wgold">{s.value}</p>
                  <p className="mt-1.5 text-sm text-wmuted">{s.label}</p>
                </div>
              </motion.div>
            ))}
          </motion.div>
        </WSection>
      )}

      {/* ── Story ─────────────────────────────────────────────────────── */}
      {page.intro?.length > 0 && (
        <WSection>
          <div className="rounded-xl2 border border-wline bg-wcard p-6 shadow-sm sm:p-8">
            <WSectionLabel className="mb-5">{page.story_label || 'Our story'}</WSectionLabel>
            <div className="max-w-3xl">
              <WProse text={page.intro.join('\n\n')} />
            </div>
          </div>
        </WSection>
      )}

      {/* ── Values ────────────────────────────────────────────────────── */}
      {page.values?.length > 0 && (
        <WSection>
          <WSectionLabel className="mb-5">{page.values_label || 'What we value'}</WSectionLabel>
          <motion.div
            variants={staggerContainer(0.06)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-60px' }}
            className="grid gap-4 sm:grid-cols-2"
          >
            {page.values.map((v) => {
              const Icon = resolvePageIcon(v.icon);
              return (
                <motion.div key={v.title} variants={fadeUp}>
                  <div className="flex gap-4 rounded-xl2 border border-wline bg-wcard p-5 shadow-sm transition-shadow hover:shadow-md">
                    <span className="grid size-10 shrink-0 place-items-center rounded-full bg-wgold/10 text-wgold">
                      <Icon className="size-5" aria-hidden="true" />
                    </span>
                    <div>
                      <h3 className="font-semibold text-wink">{v.title}</h3>
                      <p className="mt-1 text-sm leading-relaxed text-wmuted">{v.text}</p>
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </motion.div>
        </WSection>
      )}

      {/* ── Mission ───────────────────────────────────────────────────── */}
      {page.mission?.heading && (
        <WSection>
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-60px' }}
            transition={{ duration: 0.45 }}
          >
            <div className="relative overflow-hidden rounded-xl2 border border-wline bg-wcard p-6 shadow-sm sm:p-10">
              {/* Left gold accent bar */}
              <div className="absolute inset-y-0 left-0 w-1 rounded-r bg-wgold" />
              {/* The eyebrow used to be a hardcoded "Our mission", which printed
                  the words twice because mission.heading defaults to the same
                  text — and it shadowed the admin's heading, so renaming the
                  section in /admin/pages changed nothing visible. The
                  admin-managed heading is now the only label. */}
              <h2 className="max-w-2xl font-display text-[11px] font-semibold uppercase tracking-[0.22em] text-wgold">
                {page.mission.heading}
              </h2>
              <div className="mt-4 max-w-2xl">
                <WProse text={page.mission.body} />
              </div>
            </div>
          </motion.div>
        </WSection>
      )}
    </ContentPage>
  );
}
