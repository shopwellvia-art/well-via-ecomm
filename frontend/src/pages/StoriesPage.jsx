import { ArrowUpRight, Newspaper } from 'lucide-react';
import { safeUrl } from '@/lib/safeUrl.js';
import { Page } from '@/components/layout/Page.jsx';
import { Card, CardBody } from '@/components/ui/Card.jsx';
import { useSitePages } from '@/features/site-pages/hooks.js';
import { SITE_PAGES_DEFAULTS } from '@/features/site-pages/defaults.js';
import { CompanyHero, Section, PageDisabled } from '@/features/site-pages/components.jsx';

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
}

function StoryCard({ post }) {
  const inner = (
    <>
      <div className="relative aspect-[16/9] overflow-hidden bg-fill">
        {post.image ? (
          <img
            src={post.image}
            alt=""
            loading="lazy"
            className="size-full object-cover transition-transform duration-500 group-hover:scale-105"
          />
        ) : (
          <div className="grid size-full place-items-center bg-gradient-to-br from-accent/20 to-accent/5 text-accent">
            <Newspaper className="size-8" aria-hidden="true" />
          </div>
        )}
        {post.category && (
          <span className="absolute left-3 top-3 rounded-full bg-bg-elevated/90 px-2.5 py-1 text-xs font-medium text-ink-primary backdrop-blur">
            {post.category}
          </span>
        )}
      </div>
      <CardBody>
        {post.date && (
          <p className="text-xs font-medium text-ink-tertiary">{formatDate(post.date)}</p>
        )}
        <h3 className="mt-1.5 font-semibold text-ink-primary group-hover:text-accent">
          {post.title}
        </h3>
        {post.excerpt && (
          <p className="mt-1.5 line-clamp-3 text-sm leading-relaxed text-ink-secondary">
            {post.excerpt}
          </p>
        )}
        {post.url && (
          <span className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-accent">
            Read more
            <ArrowUpRight className="size-4" aria-hidden="true" />
          </span>
        )}
      </CardBody>
    </>
  );

  return (
    <Card className="group overflow-hidden transition-colors hover:border-line-strong">
      {post.url ? (
        <a href={safeUrl(post.url)} className="block rounded-lg focus-visible:focus-ring">
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
    <Page>
      <CompanyHero hero={page.hero} current="Lumen Stories">
        {page.intro && <p className="mt-5 max-w-2xl text-ink-secondary">{page.intro}</p>}
      </CompanyHero>

      <Section className="mt-12">
        {page.posts?.length > 0 ? (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {page.posts.map((post, i) => (
              <StoryCard key={i} post={post} />
            ))}
          </div>
        ) : (
          <p className="text-ink-secondary">No stories published yet. Check back soon.</p>
        )}
      </Section>
    </Page>
  );
}
