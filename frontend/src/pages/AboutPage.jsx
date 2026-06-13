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
    <>
      <CompanyHero hero={page.hero} current="About Us" />

      <Page>
        {/* Stats row */}
        {page.stats?.length > 0 && (
          <Section className="mt-6">
            <motion.div
              variants={staggerContainer(0.07)}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, margin: '-60px' }}
              className="grid grid-cols-2 gap-4 sm:grid-cols-4"
            >
              {page.stats.map((s, i) => (
                <motion.div key={i} variants={fadeUp}>
                  <Card className="text-center">
                    <CardBody className="py-6">
                      <p className="text-2xl font-bold text-accent">{s.value}</p>
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
            <Card>
              <CardBody className="p-6 sm:p-8">
                <SectionLabel className="mb-4">Our story</SectionLabel>
                <div className="max-w-3xl">
                  <Prose text={page.intro.join('\n\n')} />
                </div>
              </CardBody>
            </Card>
          </Section>
        )}

        {/* Values */}
        {page.values?.length > 0 && (
          <Section>
            <SectionLabel className="mb-4">What we value</SectionLabel>
            <motion.div
              variants={staggerContainer(0.06)}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, margin: '-60px' }}
              className="grid gap-4 sm:grid-cols-2"
            >
              {page.values.map((v, i) => {
                const Icon = resolvePageIcon(v.icon);
                return (
                  <motion.div key={i} variants={fadeUp}>
                    <Card interactive>
                      <CardBody className="flex gap-4">
                        <span className="grid size-10 shrink-0 place-items-center rounded-sm bg-accent-soft text-accent">
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

        {/* Mission — accent-bordered card, flat Flipkart style */}
        {page.mission?.heading && (
          <Section>
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: '-60px' }}
              transition={{ duration: 0.45 }}
            >
              <Card className="border-l-4 border-l-accent">
                <CardBody className="p-6 sm:p-10">
                  <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">
                    Our mission
                  </p>
                  <h2 className="mt-3 max-w-2xl text-xl font-semibold leading-snug text-ink-primary">
                    {page.mission.heading}
                  </h2>
                  <div className="mt-4 max-w-2xl">
                    <Prose text={page.mission.body} />
                  </div>
                </CardBody>
              </Card>
            </motion.div>
          </Section>
        )}
      </Page>
    </>
  );
}
