import { Link } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { useProducts, useBestsellers } from '@/features/products/hooks.js';
import HeroSection from '@/components/storefront/HeroSection';
import ProductCard from '@/components/storefront/ProductCard';
import { Stars } from '@/components/storefront/Icons';

/* ── Static content (no blog/review backends exist — copy from the mockup) ── */

const BLOG_POSTS = [
  {
    title: 'Why Sleep Is Your Superpower',
    date: '25 May 2026',
    image: '/home/blog-sleep.jpg',
  },
  {
    title: 'Gut Health Starts Here',
    date: '31 May 2026',
    image: '/home/blog-gut.jpg',
  },
  {
    title: 'Small Habits. Big Results.',
    date: '10 June 2026',
    image: '/home/blog-habits.jpg',
  },
];

const REVIEW = {
  quote:
    'Finally, a wellness routine I actually stick to. Tastes great and fits effortlessly into my day.',
  name: 'Priya S.',
  stars: 4,
};

const ROUTINES = [
  {
    key: 'morning',
    label: 'Morning Routine',
    note: 'Gut health + Immunity = A Stronger You',
    lines: ["Don't Just", 'Wake Up', 'Show UP.'],
    sub: 'Fuel your morning!',
    align: 'left',
  },
  {
    key: 'night',
    label: 'Night Routine',
    note: 'Beauty + Sleep = Glow While You Rest',
    lines: ['END YOUR', 'DAY RIGHT.'],
    sub: 'Nourish your body with restful sleep',
    align: 'right',
  },
];

/* ── Small shared pieces ─────────────────────────────────────────────────── */

function RailSkeleton({ count = 4 }) {
  return (
    <div className="flex gap-[18px] overflow-hidden">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="w-[260px] shrink-0 bg-wcard border border-wline rounded-xl2 overflow-hidden animate-pulse"
        >
          <div className="w-full h-[200px] bg-wcanvas" />
          <div className="p-[18px] space-y-3">
            <div className="h-5 bg-wcanvas rounded w-3/4" />
            <div className="h-2.5 bg-wcanvas rounded w-1/2" />
            <div className="mt-4 h-10 bg-wcanvas rounded-full w-full" />
          </div>
        </div>
      ))}
    </div>
  );
}

function InlineError({ onRetry }) {
  return (
    <div className="py-16 text-center">
      <p className="text-wmuted mb-5 text-[15px]">
        Could not load products. Please try again.
      </p>
      <button
        onClick={onRetry}
        className="bg-wgreen text-white rounded-full px-8 py-3 text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors border-0 cursor-pointer"
      >
        Retry
      </button>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function HomePage() {
  const reduce = useReducedMotion();

  /* Real data */
  const {
    data: bestsellersData,
    isLoading: bestsellersLoading,
    isError: bestsellersError,
    refetch: refetchBestsellers,
  } = useBestsellers(8);
  const bestsellers = bestsellersData ?? [];

  const { data: combosData, isLoading: combosLoading } = useProducts({
    is_combo: true,
    page_size: 8,
  });
  const combos = combosData?.items ?? [];

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
      className="overflow-hidden"
    >
      {/* 1. Hero */}
      <HeroSection />

      {/* 2. Why we Exist — spilled-gummies visual left, right-aligned copy */}
      <section
        id="why-we-exist"
        className="grid md:grid-cols-[1fr_1.05fr] gap-8 lg:gap-16 items-center px-5 sm:px-10 lg:px-16 py-12 lg:py-[84px]"
      >
        <div className="flex justify-center md:justify-start">
          {/* Tilted pouch with berry spill — brand shot from the design file */}
          <img
            src="/home/why-pouch.jpg"
            alt="Wellvia Immunity gummies spilling from the pouch"
            loading="lazy"
            className="w-[clamp(280px,38vw,520px)] h-auto rounded-xl2"
          />
        </div>

        <div className="text-right">
          <h2 className="font-wserif font-semibold text-[clamp(28px,3vw,40px)] text-wgreen m-0 mb-5">
            Why we Exist?
          </h2>
          <p className="font-wserif font-semibold text-[clamp(22px,2.2vw,30px)] text-wink m-0 mb-4">
            It started with one belief.
          </p>
          <p className="font-wserif text-[clamp(18px,1.8vw,25px)] leading-[1.55] text-wink/85 m-0 max-w-[520px] ml-auto">
            Taking care of your health shouldn&apos;t feel like a chore.
            That&apos;s why we created gummies that are enjoyable to take,
            thoughtfully formulated, and made to fit effortlessly into your
            day.
          </p>
        </div>
      </section>

      {/* 3. Product rail — bestsellers */}
      <section className="px-5 sm:px-10 lg:px-16 py-10 lg:py-[64px]">
        <h2 className="font-wserif font-semibold text-[clamp(28px,3.2vw,42px)] leading-[1.15] text-wink m-0 mb-8 lg:mb-10 max-w-[520px]">
          Your body works hard.
          <br />
          Help it a little.
        </h2>

        {bestsellersLoading ? (
          <RailSkeleton />
        ) : bestsellersError ? (
          <InlineError onRetry={refetchBestsellers} />
        ) : bestsellers.length === 0 ? (
          <p className="text-wmuted text-[15px]">
            Our rituals are restocking —{' '}
            <Link to="/products" className="text-wgreen underline underline-offset-2">
              browse the full catalog
            </Link>
            .
          </p>
        ) : (
          <div className="rail flex gap-[18px] overflow-x-auto pb-3 snap-x">
            {bestsellers.map((p) => (
              <div key={p.id} className="w-[262px] shrink-0 snap-start">
                <ProductCard product={p} />
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 4. New Launches — pale-green band (frontend-3) */}
      <section className="bg-[#f2f4ea] px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <div className="text-center mb-10">
          <h2 className="font-wserif font-semibold text-[clamp(30px,3.6vw,46px)] text-wink m-0 mb-5">
            New Launches
          </h2>
          <Link
            to="/new-arrivals"
            className="inline-block bg-wgreen text-white no-underline rounded-full px-8 py-[11px] font-wserif text-[16px] tracking-wide hover:bg-wgreen-dark transition-colors"
          >
            Explore All
          </Link>
        </div>

        <div className="grid md:grid-cols-[1.2fr_1fr] gap-10 items-center max-w-[1100px] mx-auto">
          {/* Limited-collection trio — brand shot from the design file */}
          <div className="flex items-center justify-center">
            <img
              src="/home/launch-trio.jpg"
              alt="Core Omega, Beauty Boost and Sleep gummies — the limited collection"
              loading="lazy"
              className="w-full max-w-[620px] h-auto rounded-xl2"
            />
          </div>

          {/* Limited collection copy */}
          <div className="text-center md:text-left">
            <h3 className="font-wserif font-semibold text-[clamp(26px,2.8vw,38px)] leading-[1.15] text-wgreen-deep m-0 mb-7">
              The Wellness Gummy
              <br />
              Limited Collection
            </h3>
            <Link
              to="/new-arrivals"
              className="inline-block bg-transparent border border-wgreen text-wgreen no-underline rounded-[12px] px-10 py-3.5 font-wserif text-[18px] tracking-wide hover:bg-wgreen hover:text-white transition-colors"
            >
              Shop Now!
            </Link>
          </div>
        </div>
      </section>

      {/* 5. Build Your Daily Routine — brand imagery from the design file;
             each card links to its real combo product when one exists,
             falling back to /categories until combos are added in admin. */}
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <div className="text-center mb-10 lg:mb-14">
          <h2 className="font-wserif font-semibold text-[clamp(30px,3.6vw,46px)] text-wink m-0 mb-3">
            Build Your Daily Routine
          </h2>
          <p className="font-wserif text-[clamp(18px,1.9vw,26px)] text-wink/80 m-0">
            Choose a bundle crafted for the way you live.
          </p>
        </div>

        <div className="grid md:grid-cols-2 gap-10 lg:gap-14 max-w-[1140px] mx-auto">
          {ROUTINES.map((r, i) => {
            const right = r.align === 'right';
            const combo = combos[i] ?? null;
            const target = combo ? `/products/${combo.id}` : '/categories';
            return (
              <div
                key={r.key}
                className={`flex items-center gap-6 ${right ? 'md:flex-row-reverse' : ''}`}
              >
                {/* Caption block */}
                <div className={`shrink-0 max-w-[190px] ${right ? 'text-right' : ''}`}>
                  <div className="font-wserif text-[15px] text-wink/70 underline underline-offset-4 mb-2.5">
                    {r.label}
                  </div>
                  <div className="font-wserif font-semibold text-[24px] leading-[1.2] text-wink mb-1.5">
                    {r.lines.map((l, li) => (
                      <span key={li}>
                        {li === r.lines.length - 1 ? (
                          <span className="text-wgreen">{l}</span>
                        ) : (
                          l
                        )}
                        {li < r.lines.length - 1 && <br />}
                      </span>
                    ))}
                  </div>
                  <p className="text-[13.5px] text-wmuted m-0 mb-4">{r.sub}</p>
                  <Link
                    to={target}
                    className="inline-block border border-wgreen/60 text-wgreen no-underline rounded-[10px] px-4 py-2 text-[13px] tracking-wide hover:bg-wgreen hover:text-white transition-colors whitespace-nowrap"
                  >
                    {right ? "← See What's Inside" : "See What's Inside →"}
                  </Link>
                </div>

                {/* Bundle visual — includes the hand-written pairing note */}
                <div className="flex-1">
                  <Link to={target} className="block" aria-label={combo?.name ?? r.label}>
                    <img
                      src={`/home/routine-${r.key}.jpg`}
                      alt={combo?.name ?? `${r.label} gummies bundle`}
                      loading="lazy"
                      className="w-full max-w-[420px] mx-auto h-auto rounded-xl2"
                    />
                  </Link>
                  {combo && (
                    <div className="mt-2 text-center font-wserif text-[15px] text-wink">
                      {combo.name}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* 6. Wellness, without the confusion — static blog cards */}
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[84px]">
        <div className="grid md:grid-cols-2 gap-8 items-start max-w-[1140px] mx-auto mb-10 lg:mb-14">
          <h2 className="font-display text-[clamp(26px,3vw,40px)] leading-[1.3] tracking-[0.04em] text-wink m-0">
            WELLNESS,
            <br />
            <span className="text-wgreen">WITHOUT</span> THE
            <br />
            CONFUSION.
          </h2>
          <p className="font-wserif text-[clamp(18px,1.8vw,25px)] leading-[1.55] text-wink/85 m-0 md:text-right">
            No complicated jargon. No wellness myths. Just simple insights to
            help you make better choices every day.
          </p>
        </div>

        <div className="grid sm:grid-cols-3 gap-6 lg:gap-9 max-w-[1140px] mx-auto">
          {BLOG_POSTS.map((post) => (
            <article key={post.title}>
              <img
                src={post.image}
                alt=""
                loading="lazy"
                className="w-full h-[clamp(220px,24vw,300px)] object-cover rounded-xl2 mb-5"
              />
              <h3 className="font-wserif font-semibold text-[clamp(20px,1.9vw,26px)] leading-[1.25] text-wink m-0 mb-1.5">
                {post.title}
              </h3>
              <div className="font-wserif text-[14.5px] text-wmuted mb-2">{post.date}</div>
              <Link
                to="/stories"
                className="font-wserif text-[16px] text-wgreen no-underline hover:underline underline-offset-4"
              >
                Read more→
              </Link>
            </article>
          ))}
        </div>
      </section>

      {/* 7. The Reviews Behind the Routine — static testimonial */}
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <h2 className="font-wserif font-semibold text-[clamp(28px,3.4vw,44px)] text-wink text-center m-0 mb-10">
          The Reviews Behind the Routine
        </h2>

        <div className="max-w-[1080px] mx-auto border border-wgreen/40 rounded-xl3 bg-wcard/40 p-6 sm:p-10 grid md:grid-cols-[1fr_1.25fr] gap-8 items-center">
          {/* Avatar cluster — customer photos from the design file */}
          <div className="hidden md:flex items-center justify-center" aria-hidden="true">
            <img
              src="/home/avatars.jpg"
              alt=""
              loading="lazy"
              className="w-full max-w-[340px] h-auto rounded-xl2"
            />
          </div>

          {/* Quote card */}
          <div className="relative bg-white rounded-xl2 shadow-[0_28px_60px_-34px_rgba(30,30,26,0.45)] p-7 sm:p-9">
            <span
              className="absolute top-5 right-7 font-wserif text-[64px] leading-none text-wline select-none"
              aria-hidden="true"
            >
              &rdquo;
            </span>
            <Stars count={REVIEW.stars} className="text-[22px]" />
            <blockquote className="font-wserif font-medium text-[clamp(18px,1.8vw,23px)] leading-[1.5] text-wink m-0 mt-4 mb-5">
              &ldquo;{REVIEW.quote}&rdquo;
            </blockquote>
            <div className="font-wserif text-[17px] text-wink/80">{REVIEW.name}</div>
            <span
              className="absolute -bottom-2 left-8 font-wserif text-[110px] leading-none text-wline/60 select-none"
              aria-hidden="true"
            >
              &ldquo;
            </span>
          </div>
        </div>

        <div className="text-center mt-9">
          <Link
            to="/products"
            className="font-wserif text-[19px] text-wink underline underline-offset-[6px] hover:text-wgreen transition-colors"
          >
            Check All Reviews →
          </Link>
        </div>
      </section>

      {/* 8. Brand philosophy — the puzzle piece */}
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[84px]">
        {/* 8a. the piece that brings it all together */}
        <div className="grid md:grid-cols-2 gap-10 items-center max-w-[1140px] mx-auto mb-16 lg:mb-24">
          <div>
            <h2 className="font-wserif text-[clamp(32px,3.8vw,52px)] leading-[1.15] text-wink/85 m-0 mb-5">
              the piece that brings
              <br />
              it all together.
            </h2>
            <p className="font-wserif text-[clamp(17px,1.6vw,22px)] leading-[1.5] text-wmuted m-0 max-w-[340px]">
              &ldquo;sometimes, the smallest things make the biggest
              difference.&rdquo;
            </p>
            <div className="h-px bg-wline max-w-[380px] my-8" />
            <p className="text-[17px] leading-snug text-wink m-0 mb-4 max-w-[250px]">
              Science backed supplements for everyday
            </p>
            <Link
              to="/products"
              className="inline-flex items-center gap-2.5 bg-wgreen text-white no-underline rounded-full pl-6 pr-2 py-2 text-[15px] hover:bg-wgreen-dark transition-colors"
            >
              Shop now
              <span className="w-8 h-8 rounded-full border border-white/40 grid place-items-center text-[15px]">
                ↗
              </span>
            </Link>
          </div>

          {/* Puzzle-piece brand art from the design file */}
          <div className="flex justify-center" aria-hidden="true">
            <img
              src="/home/puzzle-piece.jpg"
              alt=""
              loading="lazy"
              className="w-full max-w-[400px] h-auto rounded-xl2"
            />
          </div>
        </div>

        {/* 8b. together with what? */}
        <div className="grid md:grid-cols-[1.15fr_1fr] gap-12 items-center max-w-[1140px] mx-auto mb-16 lg:mb-24">
          {/* Pastel habit-chip cluster from the design file */}
          <div className="flex justify-center md:justify-start" aria-hidden="true">
            <img
              src="/home/puzzle-chips.jpg"
              alt=""
              loading="lazy"
              className="w-full max-w-[500px] h-auto rounded-xl2"
            />
          </div>

          <div>
            <h2 className="font-wserif text-[clamp(30px,3.4vw,46px)] text-wink/85 m-0 mb-3 md:text-right">
              together with what?
            </h2>
            <div className="h-px bg-wline mb-9" />
            <ul className="m-0 p-0 list-none font-wserif text-[clamp(20px,2vw,28px)] text-wink/85 space-y-7">
              <li>Healthy habits</li>
              <li className="pl-10 md:pl-24">Daily routine</li>
              <li className="pl-4 md:pl-10">Choices you make</li>
            </ul>
          </div>
        </div>

        {/* 8c. Closing quote */}
        <div className="grid md:grid-cols-2 gap-10 items-center max-w-[1140px] mx-auto">
          <blockquote className="font-wserif text-[clamp(26px,3vw,42px)] leading-[1.3] text-wink/85 m-0">
            &ldquo;Every healthy routine has its pieces. This is one of
            them.&rdquo;
          </blockquote>
          <div className="grid grid-cols-2 gap-4">
            {['/home/lifestyle-1.jpg', '/home/lifestyle-2.jpg'].map((src) => (
              <img
                key={src}
                src={src}
                alt="Wellvia daily ritual"
                loading="lazy"
                className="w-full h-[clamp(180px,20vw,260px)] object-cover rounded-xl2"
              />
            ))}
          </div>
        </div>
      </section>
    </motion.div>
  );
}
