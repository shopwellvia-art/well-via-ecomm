import { motion } from 'framer-motion';
import { ArrowUpRight, Download, Mail, Phone, Newspaper } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import {
  ContentPage,
  WSection,
  WSectionLabel,
} from '@/components/storefront/ContentPage.jsx';
import { listStagger, fadeUp } from '@/lib/motion.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

// ── Press release row ─────────────────────────────────────────────────────────

function Release({ item }) {
  const inner = (
    <div className="flex flex-col gap-2 p-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        {item.date && (
          <p className="text-[11px] font-semibold uppercase tracking-widest text-wmuted">
            {formatDate(item.date)}
            {item.source ? (
              <span className="before:mx-1 before:content-['·']">{item.source}</span>
            ) : null}
          </p>
        )}
        <h3 className="mt-1 font-semibold leading-snug text-wink">{item.title}</h3>
      </div>
      {item.url && (
        <span className="inline-flex shrink-0 items-center gap-1 text-sm font-semibold text-wgold">
          Read
          <ArrowUpRight className="size-4" aria-hidden="true" />
        </span>
      )}
    </div>
  );

  return (
    <div className="rounded-xl2 border border-wline bg-wcard shadow-sm transition-shadow hover:shadow-md">
      {item.url ? (
        <a
          href={safeUrl(item.url)}
          target="_blank"
          rel="noreferrer"
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

export default function PressPage() {
  // ── Data wiring (unchanged) ──────────────────────────────────────────────
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.press, ...data?.press };

  if (page.enabled === false) {
    return <ContentPage disabled disabledTitle="Press" />;
  }

  const { contact } = page;

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
      <WSection className="mt-0 grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        {/* ── Releases list ─────────────────────────────────────────── */}
        <div>
          <WSectionLabel className="mb-5">Latest coverage</WSectionLabel>
          {page.releases?.length > 0 ? (
            <motion.div
              variants={listStagger(0.04)}
              initial="hidden"
              whileInView="show"
              viewport={{ once: true, margin: '-40px' }}
              className="flex flex-col gap-3"
            >
              {page.releases.map((item, i) => (
                <motion.div key={i} variants={fadeUp}>
                  <Release item={item} />
                </motion.div>
              ))}
            </motion.div>
          ) : (
            <div className="flex flex-col items-center gap-3 rounded-xl2 border border-wline bg-wcard py-12 text-center shadow-sm">
              <span className="grid size-10 place-items-center rounded-full bg-wgold/10 text-wgold">
                <Newspaper className="size-5" aria-hidden="true" />
              </span>
              <p className="font-wserif text-lg text-wink">No announcements yet</p>
            </div>
          )}
        </div>

        {/* ── Sidebar: media contact + kit ──────────────────────────── */}
        <div className="flex flex-col gap-4">
          {contact?.heading && (
            <div className="rounded-xl2 border border-wline bg-wcard p-5 shadow-sm">
              <h3 className="font-wserif text-lg text-wink">{contact.heading}</h3>
              <ul className="mt-4 space-y-2.5 text-sm">
                {contact.email && (
                  <li>
                    <a
                      href={`mailto:${contact.email}`}
                      className="inline-flex items-center gap-2 text-wmuted transition-colors hover:text-wgold outline-none focus-visible:ring-2 focus-visible:ring-wgold/40 rounded"
                    >
                      <Mail className="size-4 text-wgold" aria-hidden="true" />
                      {contact.email}
                    </a>
                  </li>
                )}
                {contact.phone && (
                  <li>
                    <a
                      href={`tel:${contact.phone.replace(/\s/g, '')}`}
                      className="inline-flex items-center gap-2 text-wmuted transition-colors hover:text-wgold outline-none focus-visible:ring-2 focus-visible:ring-wgold/40 rounded"
                    >
                      <Phone className="size-4 text-wgold" aria-hidden="true" />
                      {contact.phone}
                    </a>
                  </li>
                )}
              </ul>
            </div>
          )}

          {page.kit_url && (
            <a
              href={safeUrl(page.kit_url)}
              className="flex items-center gap-3 rounded-xl2 border border-wline bg-wcard p-4 shadow-sm transition-all hover:border-wgold hover:shadow-md outline-none focus-visible:ring-2 focus-visible:ring-wgold/50"
            >
              <span className="grid size-10 place-items-center rounded-full bg-wgold/10 text-wgold">
                <Download className="size-5" aria-hidden="true" />
              </span>
              <div>
                <p className="font-semibold text-wink">Media kit</p>
                <p className="text-xs text-wmuted">Logos, brand assets &amp; fact sheet</p>
              </div>
            </a>
          )}
        </div>
      </WSection>
    </ContentPage>
  );
}
