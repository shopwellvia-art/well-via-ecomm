import { MapPin, Briefcase, ArrowUpRight } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
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

function Opening({ job }) {
  const inner = (
    <CardBody className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h3 className="font-semibold text-ink-primary">{job.title}</h3>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-secondary">
          {job.department && (
            <span className="inline-flex items-center gap-1">
              <Briefcase className="size-3.5" aria-hidden="true" />
              {job.department}
            </span>
          )}
          {job.location && (
            <span className="inline-flex items-center gap-1">
              <MapPin className="size-3.5" aria-hidden="true" />
              {job.location}
            </span>
          )}
          {job.type && (
            <span className="rounded-full bg-fill px-2 py-0.5 font-medium text-ink-secondary">
              {job.type}
            </span>
          )}
        </div>
      </div>
      {job.url && (
        <span className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-accent">
          Apply
          <ArrowUpRight className="size-4" aria-hidden="true" />
        </span>
      )}
    </CardBody>
  );

  return (
    <Card className="transition-colors hover:border-line-strong">
      {job.url ? (
        <a href={safeUrl(job.url)} className="block rounded-lg focus-visible:focus-ring">
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
    <Page>
      <CompanyHero hero={page.hero} current="Careers">
        {page.intro && <p className="mt-5 max-w-2xl text-ink-secondary">{page.intro}</p>}
      </CompanyHero>

      {/* Perks */}
      {page.perks?.length > 0 && (
        <Section className="mt-12">
          <SectionLabel>Why you'll love it here</SectionLabel>
          <div className="mt-6 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {page.perks.map((p, i) => {
              const Icon = resolvePageIcon(p.icon);
              return (
                <Card key={i}>
                  <CardBody>
                    <span className="grid size-11 place-items-center rounded-xl bg-accent/12 text-accent">
                      <Icon className="size-5" aria-hidden="true" />
                    </span>
                    <h3 className="mt-4 font-semibold text-ink-primary">{p.title}</h3>
                    <p className="mt-1 text-sm leading-relaxed text-ink-secondary">{p.text}</p>
                  </CardBody>
                </Card>
              );
            })}
          </div>
        </Section>
      )}

      {/* Openings */}
      <Section>
        <SectionLabel>Open roles</SectionLabel>
        {page.openings?.length > 0 ? (
          <div className="mt-6 flex flex-col gap-3">
            {page.openings.map((job, i) => (
              <Opening key={i} job={job} />
            ))}
          </div>
        ) : (
          <p className="mt-4 text-ink-secondary">
            No open roles right now — but we're always glad to hear from great people.
          </p>
        )}
      </Section>

      {/* Culture */}
      {page.culture?.heading && (
        <Section>
          <SectionLabel>{page.culture.heading}</SectionLabel>
          <div className="mt-5 max-w-3xl">
            <Prose text={page.culture.body} />
          </div>
        </Section>
      )}
    </Page>
  );
}
