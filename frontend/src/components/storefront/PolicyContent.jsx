import { motion } from 'framer-motion';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import {
  ContentPage,
  WProse,
  WSection,
  WSectionLabel,
} from '@/components/storefront/ContentPage.jsx';

/**
 * Shared renderer for the storefront legal / customer-care policy pages
 * (Privacy, Terms, Refund/Cancellation, Shipping). Each page is a hero, an
 * optional "last updated" line, and a list of heading + body sections — all
 * admin-editable via the Company Pages editor.
 *
 * Props
 * ─────
 *  pageKey        Key into the site-pages document (e.g. 'privacy').
 *  disabledTitle  Heading shown when an admin has toggled the page off.
 */
export default function PolicyContent({ pageKey, disabledTitle }) {
  const { data, isError } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS[pageKey], ...data?.[pageKey] };

  if (page.enabled === false) {
    return <ContentPage disabled disabledTitle={disabledTitle} />;
  }

  return (
    <ContentPage
      eyebrow={page.hero?.eyebrow}
      title={page.hero?.title}
      subtitle={page.hero?.subtitle}
      extra={
        page.updated ? (
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-wmuted">
            {page.updated}
          </p>
        ) : null
      }
    >
      {/* Error banner — cached defaults shown when network failed */}
      {isError && (
        <p role="alert" className="mb-6 text-xs text-red-500">
          Content could not be refreshed. Showing cached defaults.
        </p>
      )}

      {page.sections?.length > 0 && (
        <WSection className="mt-0">
          <div className="mx-auto flex max-w-3xl flex-col gap-6">
            {page.sections.map((s, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 10 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: '-60px' }}
                transition={{ duration: 0.4, delay: i * 0.04 }}
              >
                <div className="rounded-xl2 border border-wline bg-wcard p-6 shadow-sm sm:p-7">
                  <WSectionLabel className="mb-3 text-lg">{s.heading}</WSectionLabel>
                  <WProse text={s.body} />
                </div>
              </motion.div>
            ))}
          </div>
        </WSection>
      )}
    </ContentPage>
  );
}
