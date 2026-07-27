import { motion } from 'framer-motion';
import { Download, FileText, Mail, Phone } from 'lucide-react';
import { Link } from 'react-router-dom';
import { safeUrl } from '@/lib/safeUrl.js';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import {
  ContentPage,
  WProse,
  WSection,
  WSectionLabel,
} from '@/components/storefront/ContentPage.jsx';
import { staggerContainer, fadeUp, listStagger } from '@/lib/motion.js';

// ── Download link — preserves internal (Link) vs external (a) distinction ────

const downloadRowCls =
  'flex items-center gap-3 rounded-xl border border-wline bg-wpaper px-4 py-3 text-wink transition-all hover:border-wgold hover:bg-wcard outline-none focus-visible:ring-2 focus-visible:ring-wgold/50';

function DownloadLink({ item }) {
  const isInternal = item.url?.startsWith('/');
  const content = (
    <>
      <FileText className="size-4 text-wgold" aria-hidden="true" />
      <span className="flex-1 text-sm font-medium">{item.label}</span>
      <Download className="size-4 text-wmuted" aria-hidden="true" />
    </>
  );

  if (!item.url) {
    return <div className={downloadRowCls}>{content}</div>;
  }
  return isInternal ? (
    <Link to={item.url} className={downloadRowCls}>
      {content}
    </Link>
  ) : (
    <a href={safeUrl(item.url)} target="_blank" rel="noreferrer" className={downloadRowCls}>
      {content}
    </a>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function CorporatePage() {
  // ── Data wiring (unchanged) ──────────────────────────────────────────────
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.corporate, ...data?.corporate };

  if (page.enabled === false) {
    return <ContentPage disabled disabledTitle="Corporate Information" />;
  }

  const { entity } = page;

  return (
    <ContentPage
      eyebrow={page.hero?.eyebrow}
      title={page.hero?.title}
      subtitle={page.hero?.subtitle}
    >
      <WSection className="mt-0 grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        {/* ── Main: prose sections + leadership ─────────────────────── */}
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
              <div className="rounded-xl2 border border-wline bg-wcard p-6 shadow-sm">
                <WSectionLabel className="mb-3">{s.heading}</WSectionLabel>
                <WProse text={s.body} />
              </div>
            </motion.div>
          ))}

          {/* Leadership */}
          {page.leadership?.length > 0 && (
            <div>
              <WSectionLabel className="mb-5">Leadership</WSectionLabel>
              <motion.div
                variants={staggerContainer(0.07)}
                initial="hidden"
                whileInView="show"
                viewport={{ once: true, margin: '-60px' }}
                className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
              >
                {page.leadership.map((p, i) => (
                  <motion.div key={i} variants={fadeUp}>
                    <div className="flex flex-col items-center rounded-xl2 border border-wline bg-wcard py-6 text-center shadow-sm">
                      <div className="relative size-16">
                        {p.image && (
                          <img
                            src={p.image}
                            alt={p.name}
                            loading="lazy"
                            className="size-16 rounded-full object-cover ring-2 ring-wline"
                            onError={(e) => {
                              e.currentTarget.onerror = null;
                              e.currentTarget.style.display = 'none';
                              e.currentTarget.nextElementSibling?.removeAttribute('hidden');
                            }}
                          />
                        )}
                        <span
                          hidden={!!p.image}
                          className="grid size-16 place-items-center rounded-full bg-wgold/10 text-lg font-semibold text-wgold"
                        >
                          {(p.name || '?').charAt(0)}
                        </span>
                      </div>
                      <p className="mt-3 font-semibold text-wink">{p.name}</p>
                      {p.title && (
                        <p className="mt-0.5 text-xs text-wmuted">{p.title}</p>
                      )}
                    </div>
                  </motion.div>
                ))}
              </motion.div>
            </div>
          )}
        </div>

        {/* ── Sidebar: entity details + documents ───────────────────── */}
        <aside className="flex flex-col gap-4">
          {entity && (
            <div className="overflow-hidden rounded-xl2 border border-wline bg-wcard shadow-sm">
              {/* Card header */}
              <div className="border-b border-wline px-5 py-4">
                <h3 className="font-wserif text-lg text-wink">Registered entity</h3>
              </div>
              {/* Card body */}
              <div className="p-5">
                <dl className="space-y-3 text-sm">
                  {entity.name && (
                    <div>
                      <dt className="text-[11px] font-semibold uppercase tracking-widest text-wmuted">
                        Legal name
                      </dt>
                      <dd className="mt-0.5 text-wmuted">{entity.name}</dd>
                    </div>
                  )}
                  {entity.cin && (
                    <div>
                      <dt className="text-[11px] font-semibold uppercase tracking-widest text-wmuted">
                        CIN
                      </dt>
                      <dd className="mt-0.5 font-mono text-xs text-wmuted">{entity.cin}</dd>
                    </div>
                  )}
                  {entity.address_lines?.length > 0 && (
                    <div>
                      <dt className="text-[11px] font-semibold uppercase tracking-widest text-wmuted">
                        Registered office
                      </dt>
                      <dd className="mt-0.5">
                        <address className="not-italic leading-6 text-wmuted">
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
                    <div className="flex items-center gap-2 text-wmuted">
                      <Mail className="size-4 text-wgold" aria-hidden="true" />
                      <a
                        href={`mailto:${entity.email}`}
                        className="transition-colors hover:text-wgold outline-none focus-visible:ring-2 focus-visible:ring-wgold/40 rounded"
                      >
                        {entity.email}
                      </a>
                    </div>
                  )}
                  {entity.phone && (
                    <div className="flex items-center gap-2 text-wmuted">
                      <Phone className="size-4 text-wgold" aria-hidden="true" />
                      <a
                        href={`tel:${entity.phone.replace(/\s/g, '')}`}
                        className="transition-colors hover:text-wgold outline-none focus-visible:ring-2 focus-visible:ring-wgold/40 rounded"
                      >
                        {entity.phone}
                      </a>
                    </div>
                  )}
                </dl>
              </div>
            </div>
          )}

          {page.downloads?.length > 0 && (
            <div>
              <WSectionLabel className="mb-3">Documents</WSectionLabel>
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
      </WSection>
    </ContentPage>
  );
}
