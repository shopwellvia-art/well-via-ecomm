import { motion } from 'framer-motion';
import { MapPin, Briefcase, ArrowUpRight } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS, resolvePageIcon } from '@/features/site-pages/defaults.js';
import {
  CompanyHero,
  Prose,
  Section,
  SectionLabel,
  PageDisabled,
} from '@/features/site-pages/components.jsx';
import { staggerContainer, fadeUp, listStagger } from '@/lib/motion.js';

function Opening({ job }) {
  const inner = (
    <CardBody className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <h3 className="font-semibold text-ink-primary">{job.title}</h3>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-secondary">
          {job.department && (
            <span className="inline-flex items-center gap-1">
              <Briefcase className="size-3.5 text-ink-tertiary" aria-hidden="true" />
              {job.department}
            </span>
          )}
          {job.location && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="size-3.5 text-ink-tertiary" aria-hidden="true" />
              {job.location}
            </span>
          )}
          {job.type && (
            <Badge tone="neutral">{job.type}</Badge>
          )}
        </div>
      </div>
      {job.url && (
        <span className="inline-flex shrink-0 items-center gap-1.5 text-sm font-semibold text-accent">
          Apply
          <ArrowUpRight className="size-4" aria-hidden="true" />
        </span>
      )}
    </CardBody>
  );

  return (
    <Card interactive>
      {job.url ? (
        <a href={safeUrl(job.url)} className="block rounded-sm focus-visible:focus-ring">
          {inner}
        </a>
      ) : (
        inner
      )}
    </Card>
  );
}

export default function CareersPage() {
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.careers, ...data?.careers };

  if (page.enabled === false) {
    return (
      <Page>
        <PageDisabled title="Careers" />
      </Page>
    );
  }

  return (
    <>
      <CompanyHero hero={page.hero} current="Careers">
        {page.intro && (
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-secondary">
            {page.intro}
          </p>
        )}
      </CompanyHero>

      <Page>
        {/* Perks grid */}
        {page.perks?.length > 0 && (
          <Section className="mt-6">
            <SectionLabel className="mb-4">Why you&apos;ll love it here</SectionLabel>
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
                    <Card className="h-full">
                      <CardBody>
                        <span className="grid size-10 place-items-center rounded-sm bg-accent-soft text-accent">
                          <Icon className="size-5" aria-hidden="true" />
                        </span>
                        <h3 className="mt-4 font-semibold text-ink-primary">{p.title}</h3>
                        <p className="mt-1 text-sm leading-relaxed text-ink-secondary">{p.text}</p>
                      </CardBody>
                    </Card>
                  </motion.div>
                );
              })}
            </motion.div>
          </Section>
        )}

        {/* Open roles */}
        <Section>
          <SectionLabel className="mb-4">Open roles</SectionLabel>
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
            <EmptyState
              size="sm"
              icon={Briefcase}
              title="No open roles right now"
              description="We're always glad to hear from great people. Check back soon."
              bordered={false}
            />
          )}
        </Section>

        {/* Culture */}
        {page.culture?.heading && (
          <Section>
            <Card>
              <CardBody className="p-6 sm:p-8">
                <SectionLabel className="mb-4">{page.culture.heading}</SectionLabel>
                <div className="max-w-3xl">
                  <Prose text={page.culture.body} />
                </div>
              </CardBody>
            </Card>
          </Section>
        )}
      </Page>
    </>
  );
}
