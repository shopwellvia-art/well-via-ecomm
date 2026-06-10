import { motion } from 'framer-motion';
import { ArrowUpRight, Download, Mail, Phone, Newspaper } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import {
  CompanyHero,
  Section,
  SectionLabel,
  PageDisabled,
} from '@/features/site-pages/components.jsx';
import { listStagger, fadeUp } from '@/lib/motion.js';

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

function Release({ item }) {
  const inner = (
    <CardBody className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        {item.date && (
          <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-tertiary">
            {formatDate(item.date)}
            {item.source ? <span className="before:mx-1 before:content-['·']">{item.source}</span> : null}
          </p>
        )}
        <h3 className="mt-1 font-semibold leading-snug text-ink-primary">{item.title}</h3>
      </div>
      {item.url && (
        <span className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-accent">
          Read
          <ArrowUpRight className="size-4" aria-hidden="true" />
        </span>
      )}
    </CardBody>
  );

  return (
    <Card interactive>
      {item.url ? (
        <a
          href={safeUrl(item.url)}
          target="_blank"
          rel="noreferrer"
          className="block rounded-lg focus-visible:focus-ring"
        >
          {inner}
        </a>
      ) : (
        inner
      )}
    </Card>
  );
}

export default function PressPage() {
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.press, ...data?.press };

  if (page.enabled === false) {
    return (
      <Page>
        <PageDisabled title="Press" />
      </Page>
    );
  }

  const { contact } = page;

  return (
    <Page>
      <CompanyHero hero={page.hero} current="Press">
        {page.intro && (
          <p className="mt-5 max-w-2xl text-sm leading-relaxed text-ink-secondary">
            {page.intro}
          </p>
        )}
      </CompanyHero>

      <Section className="mt-12 grid gap-8 lg:grid-cols-[1.6fr_1fr]">
        {/* Releases list */}
        <div>
          <SectionLabel className="text-h3">Latest coverage</SectionLabel>
          {page.releases?.length > 0 ? (
            <motion.div
              variants={listStagger(0.04)}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, margin: '-40px' }}
              className="mt-5 flex flex-col gap-3"
            >
              {page.releases.map((item, i) => (
                <motion.div key={i} variants={fadeUp}>
                  <Release item={item} />
                </motion.div>
              ))}
            </motion.div>
          ) : (
            <div className="mt-4">
              <EmptyState
                icon={Newspaper}
                size="sm"
                title="No announcements yet"
                bordered={false}
              />
            </div>
          )}
        </div>

        {/* Sidebar: media contact + kit */}
        <div className="flex flex-col gap-4">
          {contact?.heading && (
            <Card>
              <CardBody>
                <h3 className="font-semibold text-ink-primary">{contact.heading}</h3>
                <ul className="mt-3 space-y-2.5 text-sm">
                  {contact.email && (
                    <li>
                      <a
                        href={`mailto:${contact.email}`}
                        className="inline-flex items-center gap-2 text-ink-secondary transition-colors hover:text-accent focus-visible:focus-ring"
                      >
                        <Mail className="size-4 text-accent" aria-hidden="true" />
                        {contact.email}
                      </a>
                    </li>
                  )}
                  {contact.phone && (
                    <li>
                      <a
                        href={`tel:${contact.phone.replace(/\s/g, '')}`}
                        className="inline-flex items-center gap-2 text-ink-secondary transition-colors hover:text-accent focus-visible:focus-ring"
                      >
                        <Phone className="size-4 text-accent" aria-hidden="true" />
                        {contact.phone}
                      </a>
                    </li>
                  )}
                </ul>
              </CardBody>
            </Card>
          )}

          {page.kit_url && (
            <a
              href={safeUrl(page.kit_url)}
              className="gradient-border flex items-center gap-3 rounded-lg bg-bg-elevated p-4 transition-all hover-lift focus-visible:focus-ring"
            >
              <span className="grid size-10 place-items-center rounded-xl bg-accent-soft text-accent">
                <Download className="size-5" aria-hidden="true" />
              </span>
              <div>
                <p className="font-semibold text-ink-primary">Media kit</p>
                <p className="text-xs text-ink-secondary">Logos, brand assets &amp; fact sheet</p>
              </div>
            </a>
          )}
        </div>
      </Section>
    </Page>
  );
}
