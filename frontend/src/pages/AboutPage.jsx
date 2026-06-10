import { motion } from 'framer-motion';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS, resolvePageIcon } from '@/features/site-pages/defaults.js';
import {
  CompanyHero,
  Prose,
  Section,
  SectionLabel,
  PageDisabled,
} from '@/features/site-pages/components.jsx';
import { staggerContainer, fadeUp } from '@/lib/motion.js';

export default function AboutPage() {
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.about, ...data?.about };

  if (page.enabled === false) {
    return (
      <Page>
        <PageDisabled title="About Us" />
      </Page>
    );
  }

  return (
    <Page>
      <CompanyHero hero={page.hero} current="About Us" />

      {/* Stats — animated counter cards */}
      {page.stats?.length > 0 && (
        <Section className="mt-12">
          <motion.div
            variants={staggerContainer(0.07)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-60px' }}
            className="grid grid-cols-2 gap-4 sm:grid-cols-4"
          >
            {page.stats.map((s, i) => (
              <motion.div key={i} variants={fadeUp}>
                <Card interactive className="text-center">
                  <CardBody className="py-6">
                    <p className="nums text-h1 font-bold tracking-tight text-accent">{s.value}</p>
                    <p className="mt-1.5 text-sm text-ink-secondary">{s.label}</p>
                  </CardBody>
                </Card>
              </motion.div>
            ))}
          </motion.div>
        </Section>
      )}

      {/* Story */}
      {page.intro?.length > 0 && (
        <Section>
          <SectionLabel>Our story</SectionLabel>
          <div className="mt-5 max-w-3xl">
            <Prose text={page.intro.join('\n\n')} />
          </div>
        </Section>
      )}

      {/* Values */}
      {page.values?.length > 0 && (
        <Section>
          <SectionLabel>What we value</SectionLabel>
          <motion.div
            variants={staggerContainer(0.06)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-60px' }}
            className="mt-6 grid gap-5 sm:grid-cols-2"
          >
            {page.values.map((v, i) => {
              const Icon = resolvePageIcon(v.icon);
              return (
                <motion.div key={i} variants={fadeUp}>
                  <Card interactive>
                    <CardBody className="flex gap-4">
                      <span className="grid size-11 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent">
                        <Icon className="size-5" aria-hidden="true" />
                      </span>
                      <div>
                        <h3 className="font-semibold text-ink-primary">{v.title}</h3>
                        <p className="mt-1 text-sm leading-relaxed text-ink-secondary">{v.text}</p>
                      </div>
                    </CardBody>
                  </Card>
                </motion.div>
              );
            })}
          </motion.div>
        </Section>
      )}

      {/* Mission — accent gradient banner */}
      {page.mission?.heading && (
        <Section>
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: '-60px' }}
            transition={{ duration: 0.5 }}
          >
            <Card
              className="surface-gradient overflow-hidden border-0"
              style={{
                background: 'linear-gradient(125deg,#3b39d9 0%,#6366F1 45%,#8B5CF6 100%)',
              }}
            >
              <CardBody className="p-8 sm:p-12">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/60">
                  Our mission
                </p>
                <h2 className="mt-3 max-w-2xl text-h2 leading-snug tracking-tight text-white">
                  {page.mission.heading}
                </h2>
                <div className="mt-4 max-w-2xl">
                  <Prose text={page.mission.body} className="text-white/80" />
                </div>
              </CardBody>
            </Card>
          </motion.div>
        </Section>
      )}
    </Page>
  );
}
