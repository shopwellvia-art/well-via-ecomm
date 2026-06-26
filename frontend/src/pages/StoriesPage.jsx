import { useState } from 'react';
import { motion } from 'framer-motion';
import { ArrowUpRight, Newspaper } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import { ContentPage, WSection } from '@/components/storefront/ContentPage.jsx';
import { staggerContainer, fadeUp } from '@/lib/motion.js';

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
}

// ── Story card ────────────────────────────────────────────────────────────────

function StoryCard({ post }) {
  // useState preserves imgError per-card across re-renders (unchanged logic).
  const [imgError, setImgError] = useState(false);

  const inner = (
    <>
      {/* Thumbnail */}
      <div className="relative aspect-[16/9] overflow-hidden bg-wcanvas">
        {post.image && !imgError && (
          <img
            src={post.image}
            alt={post.title}
            loading="lazy"
            className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
            onError={() => setImgError(true)}
          />
        )}
        {(!post.image || imgError) && (
          <div className="grid size-full place-items-center bg-wgold/10 text-wgold">
            <Newspaper className="size-8" aria-hidden="true" />
          </div>
        )}
        {post.category && (
          <span className="absolute left-3 top-3 rounded-full border border-wgold/40 bg-wcard/90 px-2.5 py-0.5 text-[11px] font-medium text-wgold backdrop-blur-sm">
            {post.category}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="flex flex-col gap-1.5 p-4">
        {post.date && (
          <p className="text-[11px] font-semibold uppercase tracking-wide text-wmuted">
            {formatDate(post.date)}
          </p>
        )}
        <h3 className="font-semibold leading-snug text-wink transition-colors group-hover:text-wgold">
          {post.title}
        </h3>
        {post.excerpt && (
          <p className="line-clamp-3 text-sm leading-relaxed text-wmuted">{post.excerpt}</p>
        )}
        {post.url && (
          <span
            aria-hidden="true"
            className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-wgold"
          >
            Read more
            <ArrowUpRight className="size-4" aria-hidden="true" />
          </span>
        )}
      </div>
    </>
  );

  return (
    <div className="group overflow-hidden rounded-xl2 border border-wline bg-wcard shadow-sm transition-shadow hover:shadow-md">
      {post.url ? (
        <a
          href={safeUrl(post.url)}
          aria-label={post.title}
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

export default function StoriesPage() {
  // ── Data wiring (unchanged) ──────────────────────────────────────────────
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.stories, ...data?.stories };

  if (page.enabled === false) {
    return <ContentPage disabled disabledTitle="Wellvia Stories" />;
  }

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
      <WSection className="mt-0">
        {page.posts?.length > 0 ? (
          <motion.div
            variants={staggerContainer(0.06)}
            initial="hidden"
            whileInView="show"
            viewport={{ once: true, margin: '-60px' }}
            className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
          >
            {page.posts.map((post, i) => (
              <motion.div key={i} variants={fadeUp}>
                <StoryCard post={post} />
              </motion.div>
            ))}
          </motion.div>
        ) : (
          <div className="flex flex-col items-center gap-3 rounded-xl2 border border-wline bg-wcard py-16 text-center shadow-sm">
            <span className="grid size-12 place-items-center rounded-full bg-wgold/10 text-wgold">
              <Newspaper className="size-6" aria-hidden="true" />
            </span>
            <p className="font-wserif text-lg text-wink">No stories yet</p>
            <p className="max-w-xs text-sm text-wmuted">
              No stories published yet. Check back soon.
            </p>
          </div>
        )}
      </WSection>
    </ContentPage>
  );
}
