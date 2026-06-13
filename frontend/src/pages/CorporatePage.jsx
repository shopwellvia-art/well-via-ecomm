import { motion } from 'framer-motion';
import { Download, FileText, Mail, Phone } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardBody, CardHeader } from '@/components/ui/Card.jsx';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import { safeUrl } from '@/lib/safeUrl.js';
import {
  CompanyHero,
  Prose,
  Section,
  SectionLabel,
  PageDisabled,
} from '@/features/site-pages/components.jsx';
import { staggerContainer, fadeUp, listStagger } from '@/lib/motion.js';

function DownloadLink({ item }) {
  const isInternal = item.url?.startsWith('/');
  const content = (
    <>
      <FileText className="size-4 text-accent" aria-hidden="true" />
      <span className="flex-1 text-sm font-medium">{item.label}</span>
      <Download className="size-4 text-ink-tertiary" aria-hidden="true" />
    </>
  );
  const cls =
    'flex items-center gap-3 rounded-sm border border-line-subtle bg-bg-elevated px-4 py-3 text-ink-primary transition-colors hover:border-accent hover:bg-accent-soft focus-visible:focus-ring';

  if (!item.url) {
    return <div className={cls}>{content}</div>;
  }
  return isInternal ? (
    <Link to={item.url} className={cls}>
      {content}
    </Link>
  ) : (
    <a href={safeUrl(item.url)} target="_blank" rel="noreferrer" className={cls}>
      {content}
    </a>
  );
}

export default function CorporatePage() {
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.corporate, ...data?.corporate };

  if (page.enabled === false) {
    return (
      <Page>
        <PageDisabled title="Corporate Information" />
      </Page>
    );
  }

  const { entity } = page;

  return (
    <>
      <CompanyHero hero={page.hero} current="Corporate Information" />

      <Page>
        <Section className="mt-6 grid gap-6 lg:grid-cols-[1.5fr_1fr]">
          {/* Main: prose sections + leadership */}
          <div className="flex flex-col gap-8">
            {/* Text sections */}
            {page.sections?.map((s, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 10 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-60px' }}
                transition={{ duration: 0.4, delay: i * 0.05 }}
              >
                <Card>
                  <CardBody className="p-6">
                    <SectionLabel className="mb-3">{s.heading}</SectionLabel>
                    <Prose text={s.body} />
                  </CardBody>
                </Card>
              </motion.div>
            ))}

            {/* Leadership */}
            {page.leadership?.length > 0 && (
              <div>
                <SectionLabel className="mb-4">Leadership</SectionLabel>
                <motion.div
                  variants={staggerContainer(0.07)}
                  initial="hidden"
                  whileInView="show"
                  viewport={{ once: true, margin: '-60px' }}
                  className="grid gap-4 sm:grid-cols-3"
                >
                  {page.leadership.map((p, i) => (
                    <motion.div key={i} variants={fadeUp}>
                      <Card>
                        <CardBody className="flex flex-col items-center text-center py-6">
                          {p.image ? (
                            <img
                              src={p.image}
                              alt=""
                              loading="lazy"
                              className="size-16 rounded-full object-cover ring-2 ring-line-subtle"
                            />
                          ) : (
                            <span className="grid size-16 place-items-center rounded-full bg-accent-soft text-lg font-semibold text-accent">
                              {(p.name || '?').charAt(0)}
                            </span>
                          )}
                          <p className="mt-3 font-semibold text-ink-primary">{p.name}</p>
                          {p.title && (
                            <p className="mt-0.5 text-xs text-ink-secondary">{p.title}</p>
                          )}
                        </CardBody>
                      </Card>
                    </motion.div>
                  ))}
                </motion.div>
              </div>
            )}
          </div>

          {/* Sidebar: entity details + documents */}
          <aside className="flex flex-col gap-4">
            {entity && (
              <Card>
                <CardHeader title="Registered entity" />
                <CardBody>
                  <dl className="space-y-3 text-sm">
                    <div>
                      <dt className="text-[11px] font-semibold uppercase tracking-widest text-ink-tertiary">
                        Legal name
                      </dt>
                      <dd className="mt-0.5 text-ink-secondary">{entity.name}</dd>
                    </div>
                    {entity.cin && (
                      <div>
                        <dt className="text-[11px] font-semibold uppercase tracking-widest text-ink-tertiary">
                          CIN
                        </dt>
                        <dd className="mt-0.5 font-mono text-xs text-ink-secondary">
                          {entity.cin}
                        </dd>
                      </div>
                    )}
                    {entity.address_lines?.length > 0 && (
                      <div>
                        <dt className="text-[11px] font-semibold uppercase tracking-widest text-ink-tertiary">
                          Registered office
                        </dt>
                        <dd className="mt-0.5">
                          <address className="not-italic leading-6 text-ink-secondary">
                            {entity.address_lines.map((line, li) => (
                              <span key={li} className="block">
                                {line}
                              </span>
                            ))}
                          </address>
                        </dd>
                      </div>
                    )}
                    {entity.email && (
                      <div className="flex items-center gap-2 text-ink-secondary">
                        <Mail className="size-4 text-accent" aria-hidden="true" />
                        <a
                          href={`mailto:${entity.email}`}
                          className="transition-colors hover:text-accent focus-visible:focus-ring"
                        >
                          {entity.email}
                        </a>
                      </div>
                    )}
                    {entity.phone && (
                      <div className="flex items-center gap-2 text-ink-secondary">
                        <Phone className="size-4 text-accent" aria-hidden="true" />
                        <a
                          href={`tel:${entity.phone.replace(/\s/g, '')}`}
                          className="transition-colors hover:text-accent focus-visible:focus-ring"
                        >
                          {entity.phone}
                        </a>
                      </div>
                    )}
                  </dl>
                </CardBody>
              </Card>
            )}

            {page.downloads?.length > 0 && (
              <div>
                <h3 className="mb-3 text-sm font-semibold text-ink-primary">Documents</h3>
                <motion.div
                  variants={listStagger(0.04)}
                  initial="hidden"
                  whileInView="show"
                  viewport={{ once: true, margin: '-40px' }}
                  className="flex flex-col gap-2"
                >
                  {page.downloads.map((d, i) => (
                    <motion.div key={i} variants={fadeUp}>
                      <DownloadLink item={d} />
                    </motion.div>
                  ))}
                </motion.div>
              </div>
            )}
          </aside>
        </Section>
      </Page>
    </>
  );
}
