import { motion } from 'framer-motion';
import { ArrowUpRight, Newspaper } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import { CompanyHero, Section, PageDisabled } from '@/features/site-pages/components.jsx';
import { staggerContainer, fadeUp } from '@/lib/motion.js';

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
}

function StoryCard({ post }) {
  const inner = (
    <>
      {/* Thumbnail */}
      <div className="relative aspect-[16/9] overflow-hidden bg-bg-sunken">
        {post.image ? (
          <img
            src={post.image}
            alt=""
            loading="lazy"
            className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
          />
        ) : (
          <div className="grid size-full place-items-center bg-accent-soft text-accent">
            <Newspaper className="size-8" aria-hidden="true" />
          </div>
        )}
        {post.category && (
          <span className="absolute left-3 top-3">
            <Badge tone="accent" outline>{post.category}</Badge>
          </span>
        )}
      </div>

      {/* Body */}
      <CardBody className="flex flex-col gap-1.5 p-4">
        {post.date && (
          <p className="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">
            {formatDate(post.date)}
          </p>
        )}
        <h3 className="font-semibold leading-snug text-ink-primary transition-colors group-hover:text-accent">
          {post.title}
        </h3>
        {post.excerpt && (
          <p className="line-clamp-3 text-sm leading-relaxed text-ink-secondary">
            {post.excerpt}
          </p>
        )}
        {post.url && (
          <span className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-accent">
            Read more
            <ArrowUpRight className="size-4" aria-hidden="true" />
          </span>
        )}
      </CardBody>
    </>
  );

  return (
    <Card interactive className="group overflow-hidden">
      {post.url ? (
        <a href={safeUrl(post.url)} className="block rounded-sm focus-visible:focus-ring">
          {inner}
        </a>
      ) : (
        inner
      )}
    </Card>
  );
}

export default function StoriesPage() {
  const { data } = useSitePages();
  const page = { ...SITE_PAGES_DEFAULTS.stories, ...data?.stories };

  if (page.enabled === false) {
    return (
      <Page>
        <PageDisabled title="Lumen Stories" />
      </Page>
    );
  }

  return (
    <>
      <CompanyHero hero={page.hero} current="Lumen Stories">
        {page.intro && (
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-secondary">
            {page.intro}
          </p>
        )}
      </CompanyHero>

      <Page>
        <Section className="mt-6">
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
            <EmptyState
              icon={Newspaper}
              size="sm"
              title="No stories yet"
              description="No stories published yet. Check back soon."
              bordered={false}
            />
          )}
        </Section>
      </Page>
    </>
  );
}
