import { Link, useLocation, useNavigate } from 'react-router-dom';
import { motion, useReducedMotion } from 'framer-motion';
import { useProducts, useBestsellers } from '@/features/products/hooks.js';
import HeroSection from '@/components/storefront/HeroSection';
import PageMeta from '@/components/storefront/PageMeta.jsx';
import JsonLd from '@/components/storefront/JsonLd.jsx';
import { buildOrganizationSchema, buildWebSiteSchema } from '@/lib/productSchema.js';
import WImage from '@/components/storefront/WImage';
import { Stars } from '@/components/storefront/Icons';
import { Heart, ShoppingCart } from "lucide-react";
import { Fragment, useEffect, useState } from 'react';
import { AnimatePresence } from "framer-motion";
import { formatPrice } from '@/lib/utils';
import { useStorefrontConfigWithDefaults } from '@/features/storefront-config/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useAddToCart } from '@/features/cart/hooks';
import {
  useIsInWishlist,
  useAddToWishlist,
  useRemoveFromWishlist,
} from '@/features/wishlist/hooks';
import { toast } from '@/components/ui/Toaster.jsx';

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
const REVIEWS = [
  {
    quote:
      "Finally, a wellness routine I actually stick to. Tastes great and fits effortlessly into my day.",
    name: "Priya S.",
    stars: 5,
  },
  {
    quote:
      "The gummies are delicious and I've actually been consistent for the first time.",
    name: "Ananya R.",
    stars: 5,
  },
  {
    quote:
      "Loved the packaging, the taste, and the results. Highly recommend!",
    name: "Rohan M.",
    stars: 4,
  },
  {
    quote:
      "My sleep quality improved within a couple of weeks. Amazing experience.",
    name: "Sneha K.",
    stars: 5,
  },
  {
    quote:
      "Simple, effective, and something I genuinely look forward to every day.",
    name: "Aarav P.",
    stars: 5,
  },
];

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
    sub: 'Nourish your body with restful & peaceful sleep',
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
//
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

/**
 * BestsellerCard — homepage rail card over a real API product.
 * Same wiring as ProductCard (cart, wishlist, guest behaviour) with the
 * homepage's own card design.
 */
function BestsellerCard({ product }) {
  const addToCart = useAddToCart();
  const inWishlist = useIsInWishlist(product.id);
  const addWishlist = useAddToWishlist();
  const removeWishlist = useRemoveFromWishlist();
  const isSignedIn = useAuthStore((s) => !!s.accessToken);
  const navigate = useNavigate();
  const location = useLocation();

  const { id, name, price, compare_at_price, image_url, stock } = product;
  const isDiscounted =
    compare_at_price != null && Number(compare_at_price) > Number(price);
  const outOfStock = stock <= 0;

  const handleWishlist = (e) => {
    e.preventDefault();
    e.stopPropagation();
    // Wishlist is server-only by design — guests are sent to sign in.
    if (!isSignedIn) {
      navigate(`/login?next=${encodeURIComponent(location.pathname + location.search)}`);
      return;
    }
    const onError = (err) =>
      toast.error(
        err?.response?.data?.error?.message ||
          'Could not update your wishlist. Please try again.',
      );
    if (inWishlist) {
      removeWishlist.mutate(id, { onError });
    } else {
      addWishlist.mutate(id, { onError });
    }
  };

  const handleAddToCart = () => {
    if (outOfStock) return;
    addToCart.mutate(
      { productId: id, quantity: 1 },
      {
        onError: (err) =>
          toast.error(
            err?.response?.data?.error?.message ||
              'Could not add to cart. Please try again.',
          ),
      },
    );
  };

  /* Card chrome is Kavya's design from the kavya branch (bordered tile, tinted
     image box, full-bleed dark CTA with a cart icon). Her version rendered a
     hardcoded DEMO_PRODUCTS array and its buttons were stubs
     (`console.log("Add to cart")`, a <Link to="/wishlist">), so only the
     styling was taken — the behaviour below is the real wiring. */
  return (
    <div className="shrink-0 w-[60%] sm:w-[35%] md:w-[260px] lg:w-[280px] snap-start border border-[#EBE8E0] bg-[#FAF9F6] rounded-xl shadow-[0_2px_6px_rgba(0,0,0,0.02)] overflow-hidden flex flex-col">
      {/* Product image box */}
      <div className="relative aspect-[4/3] md:aspect-[16/9] w-full bg-[#F3F2EE] flex items-center justify-center p-2.5">
        <Link to={`/products/${id}`} className="block w-full h-full">
          <WImage
            src={image_url}
            alt={name}
            className="w-full h-full object-contain max-h-[80%]"
          />
        </Link>

        {/* Wishlist — a real mutation, with the guest redirect */}
        <button
          onClick={handleWishlist}
          aria-label={inWishlist ? 'Remove from wishlist' : 'Add to wishlist'}
          className={`absolute top-2 right-2 w-6 h-6 rounded-full bg-white/80 shadow-sm flex items-center justify-center hover:bg-white transition border border-gray-100 ${
            inWishlist ? 'text-red-500' : 'text-[#08112C]'
          }`}
        >
          <Heart size={12} fill={inWishlist ? 'currentColor' : 'none'} />
        </button>
      </div>

      {/* Card body */}
      <div className="flex-grow flex flex-col justify-between pt-2.5 lg:pt-2">
        <div className="text-left px-3 pb-2 lg:px-4 lg:pb-2">
          <Link to={`/products/${id}`} className="no-underline">
            <h3 className="font-wserif font-semibold text-[13px] sm:text-[14px] lg:text-[16px] leading-snug text-[#08112C] m-0 truncate">
              {name}
            </h3>
          </Link>
          <p className="font-wserif text-[12px] sm:text-[13px] lg:text-[15px] text-[#08112C] m-0 mt-0.5">
            {formatPrice(price)}
            {isDiscounted && (
              <span className="ml-2 text-wmuted font-normal line-through">
                {formatPrice(compare_at_price)}
              </span>
            )}
          </p>
        </div>

        {outOfStock ? (
          <button
            disabled
            className="w-full bg-wline/60 text-wmuted rounded-b-[11px] rounded-t-none py-2 lg:py-1.5 px-3 text-[11px] sm:text-[12px] lg:text-[13px] font-semibold tracking-wide border-0 cursor-not-allowed"
          >
            Out of Stock
          </button>
        ) : (
          <button
            onClick={handleAddToCart}
            disabled={addToCart.isPending}
            className="w-full bg-[#08112C] text-white rounded-b-[11px] rounded-t-none py-2 lg:py-1.5 px-3 flex items-center justify-center gap-1.5 text-[11px] sm:text-[12px] lg:text-[13px] font-semibold tracking-wide hover:bg-[#010E37] transition-colors border-0 cursor-pointer disabled:opacity-60"
          >
            {addToCart.isPending ? 'Adding…' : 'Add to Cart'}
            <ShoppingCart size={13} />
          </button>
        )}
      </div>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */

export default function HomePage() {
  const reduce = useReducedMotion();
  const [reviewIndex, setReviewIndex] = useState(0);

useEffect(() => {
  const interval = setInterval(() => {
    setReviewIndex((prev) => (prev + 1) % REVIEWS.length);
  }, 5000);

  return () => clearInterval(interval);
}, []);

  /* Real data */
  const {
    data: bestsellersData,
    isLoading: bestsellersLoading,
    isError: bestsellersError,
    refetch: refetchBestsellers,
  } = useBestsellers(8);
  const bestsellers = bestsellersData ?? [];

  const { data: combosData } = useProducts({
    is_combo: true,
    page_size: 8,
  });
  const combos = combosData?.items ?? [];

  /* Admin-managed section order/visibility. A non-empty cfg title overrides a
     section's heading (hero copy comes from hero-slides instead). */
  const { config } = useStorefrontConfigWithDefaults();
  const sections = (config.homepage_sections ?? []).filter(
    (s) => s.visible !== false,
  );
  const whyWeExistVisible = sections.some((s) => s.key === 'why-we-exist');

  const sectionRenderers = {
    /* 1. Hero */
    hero: () => <HeroSection showWhyButton={whyWeExistVisible} />,

    /* 2. Why we Exist — spilled-gummies visual left, right-aligned copy */
    'why-we-exist': (title) => (
      <section
  id="why-we-exist"
  className="grid grid-cols-[1.7fr_1fr] md:grid-cols-[1.2fr_1fr] gap-2 sm:gap-6 lg:gap-12 items-center md:items-start pl-0 pr-3 sm:pr-10 lg:pr-16 py-6 lg:py-[40px]"
>
  <div className="flex flex-col items-start w-full">
    {/* Small gummy accent that overlaps the pouch shot below it */}
    <img
      src="/gummy3.png"
      alt=""
      aria-hidden="true"
      className="w-[105px] sm:w-[130px] md:w-[210px] lg:w-[250px] -mb-12 md:-mb-28 lg:-mb-32 ml-2 sm:ml-16 md:ml-20 lg:ml-24 relative z-10"
    />

    {/* Tilted pouch with berry spill — bleeds off the left edge on mobile */}
    <img
      src="/homepage2.png"
      alt="Wellvia Immunity gummies spilling from the pouch"
      loading="lazy"
      className="block w-[115%] max-w-none sm:w-full sm:max-w-[440px] md:w-[110%] md:max-w-[620px] lg:max-w-[720px] h-auto rounded-r-xl2 md:rounded-r-2xl -ml-[7.5%] sm:ml-0"
    />
  </div>

  <div className="text-right pl-0 sm:pl-6 lg:pl-12 md:mt-24 lg:mt-36">
    <h2 className="font-cormorant font-semibold text-[24px] sm:text-[28px] md:text-[clamp(32px,3.5vw,48px)] text-[#08112C] m-0 mb-1.5 md:mb-5 lg:mb-12">
      {title || 'Why we Exist?'}
    </h2>
    <p className="font-cormorant font-semibold text-[16px] sm:text-[20px] md:text-[clamp(24px,2.5vw,34px)] text-wink m-0 mb-1 md:mb-4 lg:mb-8 whitespace-nowrap">
      It started with one belief.
    </p>
    <p className="font-cormorant text-[14px] sm:text-[16px] md:text-[clamp(20px,2vw,28px)] leading-[1.3] md:leading-[1.55] text-wink/85 m-0 max-w-[280px] xs:max-w-[320px] sm:max-w-full md:max-w-[580px] ml-auto">
      Taking care of your health shouldn&apos;t feel like a chore. That&apos;s
      why we created gummies that are enjoyable to take, thoughtfully
      formulated, and made to fit effortlessly into your day.
    </p>
  </div>
</section>
    ),

    /* 3. Product rail — bestsellers (hidden entirely when the API returns none) */
    'bestsellers-rail': (title) =>
      (bestsellersLoading || bestsellersError || bestsellers.length > 0) && (
        <section className="px-5 sm:px-10 lg:px-16 py-8 lg:pt-0 lg:pb-[48px] lg:-mt-44">
          <div className="flex items-start justify-between mb-8 lg:mb-6 max-w-[1080px] mx-auto">
            <h2 className="font-wserif font-semibold text-[clamp(24px,2.8vw,36px)] leading-[1.15] text-[#08112C] m-0 max-w-[460px]">
              {title || (
                <>
                  Your body works hard.
                  <br />
                  Help it a little.
                </>
              )}
            </h2>

            {/* Floating gummy accent */}
            <img
              src="/gummy2.png"
              alt=""
              className="block w-14 sm:w-12 md:w-16 lg:w-24 h-auto -translate-y-2"
              aria-hidden="true"
            />
          </div>

          {bestsellersLoading ? (
            <RailSkeleton />
          ) : bestsellersError ? (
            <InlineError onRetry={refetchBestsellers} />
          ) : (
            /* Horizontal scroll on every breakpoint, with a visible slim thumb */
            <div className="flex gap-3 md:gap-4 lg:gap-6 overflow-x-auto snap-x snap-mandatory -mx-5 px-5 md:mx-auto md:px-0 pb-4 max-w-[1080px] lg:max-w-[1280px] [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-track]:bg-transparent">
              {bestsellers.map((p) => (
                <BestsellerCard key={p.id} product={p} />
              ))}
            </div>
          )}
        </section>
      ),

    /* 4. New Launches — pale-green band (frontend-3) */
    'new-launches': (title) => (
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <div className="text-center mb-6 md:mb-10">
          <h2 className="font-wserif font-semibold text-[26px] sm:text-[32px] md:text-[clamp(30px,3.6vw,46px)] text-wink m-0 mb-3 md:mb-5">
            {title || 'New Launches'}
          </h2>
          <Link
            to="/new-arrivals"
            className="hidden md:inline-block bg-[#08112C] text-white no-underline rounded-full px-8 py-[11px] font-wserif text-[16px] tracking-wide hover:bg-[#010E37] transition-colors"
          >
            Explore All
          </Link>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-[1.2fr_1fr] gap-4 md:gap-10 items-center max-w-[1100px] mx-auto">
          {/* Limited-collection trio — brand shot from the design file */}
          <div className="order-2 md:order-1 flex items-center justify-center">
            <img
              src="/home/launch-trio.jpg"
              alt="Core Omega, Beauty Boost and Sleep gummies — the limited collection"
              loading="lazy"
              className="w-full max-w-[620px] h-auto rounded-xl2"
            />
          </div>

          {/* Limited collection copy */}
        {/* Limited collection copy — text + button in a row on mobile */}
    <div className="order-1 md:order-2 flex flex-row items-center justify-between gap-3 md:block md:text-left">
      <h3 className="font-wserif font-semibold text-[16px] sm:text-[20px] md:text-[clamp(26px,2.8vw,38px)] leading-[1.15] text-wgreen-deep m-0 md:mb-7 max-w-[60%] md:max-w-none">
        The Wellness Gummy
        <br className="hidden md:block" />
        {' '}Limited Collection
      </h3>
      <Link
        to="/new-arrivals"
        className="shrink-0 inline-block bg-transparent border border-[#08112C] text-[#08112C] no-underline rounded-[10px] md:rounded-[12px] px-4 py-2 md:px-10 md:py-3.5 font-wserif text-[13px] md:text-[18px] tracking-wide hover:bg-[#010E37] hover:text-white transition-colors whitespace-nowrap"
      >
        Shop Now!
      </Link>
    </div>
  </div>
</section>
    ),

    /* 5. Build Your Daily Routine — brand imagery from the design file;
           each card links to its real combo product when one exists,
           falling back to /categories until combos are added in admin. */
    'daily-routine': (title) => (
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <div className="text-center mb-10 lg:mb-14">
          <h2 className="font-wserif font-semibold text-[clamp(30px,3.6vw,46px)] text-wink m-0 mb-3">
            {title || 'Build Your Daily Routine'}
          </h2>
          <p className="font-wserif text-[clamp(18px,1.9vw,26px)] text-wink/80 m-0">
            Choose a bundle crafted for the way you live.
          </p>
        </div>

     <div className="grid grid-cols-1 md:grid-cols-2 gap-8 md:gap-10 lg:gap-14 max-w-[1140px] mx-auto">
  {ROUTINES.map((r, i) => {
    const right = r.align === 'right';
    const combo = combos[i] ?? null;
    const target = combo ? `/products/${combo.id}` : '/categories';
    return (
      <div
        key={r.key}
        className={`flex items-center gap-3 md:gap-6 ${right ? 'flex-row-reverse' : ''}`}
      >
        {/* Caption block */}
        <div className={`shrink-0 max-w-[42%] md:max-w-[190px] ${right ? 'text-right' : ''}`}>
          <div className="font-wserif text-[11px] sm:text-[13px] md:text-[15px] text-wink/70 underline underline-offset-4 mb-1.5 md:mb-2.5">
            {r.label}
          </div>
          <div className="font-wserif font-semibold text-[15px] sm:text-[19px] md:text-[24px] leading-[1.2] text-wink mb-1 md:mb-1.5">
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
          <p className="text-[10px] sm:text-[12px] md:text-[13.5px] text-wmuted m-0 mb-2 md:mb-4">{r.sub}</p>
          <Link
            to={target}
            className="inline-block border border-[#08112C]/60 text-[#08112C] no-underline rounded-[10px] px-3 py-1.5 md:px-4 md:py-2 text-[10px] sm:text-[12px] md:text-[13px] tracking-wide hover:bg-[#010E37] hover:text-white transition-colors whitespace-nowrap"
          >
            {right ? "← See What's Inside" : "See What's Inside →"}
          </Link>
        </div>

        {/* Bundle visual */}
        <div className="flex-1 min-w-0">
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
    ),

    /* 6. Wellness, without the confusion — static blog cards */
    blog: (title) => (
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[84px]">
  {/* Tighter desktop gap under the header, and the intro copy bottom-aligned
      to the heading, so the band does not carry dead space (kavya branch). */}
  <div className="grid grid-cols-2 gap-4 md:gap-8 items-start max-w-[1140px] mx-auto mb-4 lg:mb-2">
    <h2 className="font-cormorant text-[18px] sm:text-[24px] md:text-[clamp(26px,3vw,40px)] leading-[1.3] tracking-[0.04em] text-wink m-0">
            {title || (
              <>
                WELLNESS,
                <br />
                <span className="text-[#08112C]">WITHOUT</span> THE
                <br />
                CONFUSION.
              </>
            )}
          </h2>
          <p className="font-cormorant text-[12px] sm:text-[15px] md:text-[clamp(18px,1.8vw,25px)] leading-[1.4] md:leading-[1.6] text-wink/85 m-0 text-right md:text-right max-w-full md:max-w-[360px] ml-auto lg:self-end lg:mb-3">
            No complicated jargon. No wellness myths. Just simple insights to
            help you make better choices every day.
          </p>
        </div>

       <div className="flex sm:grid sm:grid-cols-3 gap-4 lg:gap-9 max-w-[1140px] mx-auto overflow-x-auto sm:overflow-visible snap-x snap-mandatory sm:snap-none -mx-5 px-5 sm:mx-auto sm:px-0 pb-2 scrollbar-hide">
    {BLOG_POSTS.map((post) => (
      <article key={post.title} className="shrink-0 w-[70%] sm:w-auto snap-start">
        <img
          src={post.image}
          alt=""
          loading="lazy"
          className="w-full h-[160px] sm:h-[clamp(220px,24vw,300px)] object-contain sm:object-cover bg-wcanvas rounded-xl2 mb-3 sm:mb-5"
        />
        <h3 className="font-wserif font-semibold text-[15px] sm:text-[clamp(20px,1.9vw,26px)] leading-[1.25] text-wink m-0 mb-1 sm:mb-1.5">
          {post.title}
        </h3>
        <div className="font-wserif text-[11px] sm:text-[14.5px] text-wmuted mb-1.5 sm:mb-2">{post.date}</div>
        <Link
          to="/stories"
          className="font-wserif text-[13px] sm:text-[16px] text-[#08112C] no-underline hover:underline underline-offset-4"
        >
          Read more→
        </Link>
            </article>
          ))}
        </div>
      </section>
    ),

    /* 7. The Reviews Behind the Routine — static testimonial */
    reviews: (title) => (
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <h2 className="font-wserif font-semibold text-[clamp(28px,3.4vw,44px)] text-wink text-center m-0 mb-10">
          {title || 'The Reviews Behind the Routine'}
        </h2>

        <div className="max-w-[1080px] mx-auto border border-wgreen/40 rounded-xl3 bg-wcard/40 p-4 sm:p-6 md:p-10 grid grid-cols-[0.8fr_1.2fr] md:grid-cols-[1fr_1.25fr] gap-3 sm:gap-6 md:gap-8 items-center">
  {/* Avatar cluster — now visible on mobile too */}
  <div className="flex items-center justify-center" aria-hidden="true">
    <img
      src="/home/avatars.jpg"
      alt=""
      loading="lazy"
      className="w-full max-w-[110px] sm:max-w-[180px] md:max-w-[340px] h-auto rounded-xl2"
    />
  </div>

  {/* Quote card */}
  <div className="relative bg-white rounded-xl2 shadow-[0_28px_60px_-34px_rgba(30,30,26,0.45)] px-4 pt-4 pb-0 sm:px-6 sm:pt-6 sm:pb-1 md:px-9 md:pt-9 md:pb-2">
    <span
      className="absolute top-2 right-3 sm:top-5 sm:right-7 font-wserif text-[32px] sm:text-[48px] md:text-[64px] leading-none text-wline select-none"
      aria-hidden="true"
    >
      &rdquo;
    </span>

    <AnimatePresence mode="wait">
      <motion.div
        key={reviewIndex}
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -20 }}
        transition={{ duration: 0.7 }}
      >
        <Stars count={REVIEWS[reviewIndex].stars} className="text-[14px] sm:text-[18px] md:text-[22px]" />

        <blockquote className="font-wserif font-medium text-[13px] sm:text-[17px] md:text-[clamp(18px,1.8vw,23px)] leading-[1.4] md:leading-[1.5] text-wink m-0 mt-2 sm:mt-4 mb-2 sm:mb-5">
          &ldquo;{REVIEWS[reviewIndex].quote}&rdquo;
        </blockquote>

        <div className="font-wserif text-[12px] sm:text-[15px] md:text-[17px] text-wink/80">
  {REVIEWS[reviewIndex].name}
</div>

<div className="flex justify-start mt-2">
  <span
    className="font-wserif text-[42px] sm:text-[56px] md:text-[72px] leading-none text-wline/60 select-none"
    aria-hidden="true"
  >
    &ldquo;
  </span>
</div>
      </motion.div>
    </AnimatePresence>
  </div>
</div>
        <div className="text-center mt-4">
          <Link
            to="/products"
            className="font-wserif text-[19px] text-wink underline underline-offset-[6px] hover:text-wgreen transition-colors"
          >
            Check All Reviews →
          </Link>
        </div>
      </section>
    ),

    /* 8. Brand philosophy — the puzzle piece */
    'brand-philosophy': (title) => (
      <section className="px-5 sm:px-10 lg:px-16 pt-2 pb-12 lg:pt-8 lg:pb-[60px]">
  {/* 8a. the piece that brings it all together */}
  <div className="grid grid-cols-2 md:grid-cols-2 gap-4 md:gap-10 items-center max-w-[1140px] mx-auto mb-8 md:mb-16 lg:mb-24">
    <div>
      <h2 className="font-wserif text-[18px] sm:text-[26px] md:text-[clamp(32px,3.8vw,52px)] leading-[1.2] md:leading-[1.15] text-wink/85 m-0 mb-2 md:mb-5">
        {title || (
          <>
            the piece that brings
            <br />
            it all together.
          </>
        )}
      </h2>
      <p className="font-wserif text-[11px] sm:text-[15px] md:text-[clamp(17px,1.6vw,22px)] leading-[1.4] md:leading-[1.5] text-wmuted m-0 max-w-[340px]">
        &ldquo;sometimes, the smallest things make the biggest
        difference.&rdquo;
      </p>
    </div>

    <div className="flex justify-center" aria-hidden="true">
      <img
        src="/home/puzzle-piece.jpg"
        alt=""
        loading="lazy"
        className="w-full max-w-[160px] sm:max-w-[260px] md:max-w-[400px] h-auto rounded-xl2"
      />
    </div>
  </div>

  <div className="flex justify-center items-center gap-4 sm:gap-8 md:gap-16 mt-4 md:mt-8">
    <img
      src="/homepage download.png"
      alt=""
      className="w-24 sm:w-32 md:w-52 translate-y-20 md:translate-y-32"
    />

    <div className="text-center">
      <p className="font-cormorant text-[11px] sm:text-[14px] md:text-[17px] leading-[1.3] md:leading-none text-wink mb-2 md:mb-5 max-w-[600px]">
        Science backed supplements for everyday
      </p>

      <Link
        to="/products"
        className="inline-flex items-center gap-1.5 md:gap-2.5 bg-[#08112C] text-white rounded-full pl-4 pr-1.5 py-1.5 md:pl-6 md:pr-2 md:py-2 text-[11px] md:text-[15px]"
      >
        Shop now
        <span className="w-6 h-6 md:w-8 md:h-8 rounded-full border border-white/40 grid place-items-center text-[11px] md:text-base">
          ↗
        </span>
      </Link>
    </div>

    <div className="flex flex-col items-center">
  <img
    src="/homepage question.png"
    alt=""
    className="w-16 sm:w-24 md:w-40 translate-y-20 md:translate-y-32"
  />

  {/* Mobile only */}
  <h2 className="block md:hidden mt-1 font-cormorant text-[20px] text-wink/85 text-center mt-20">
    together with what?
  </h2>
</div>
  </div>

 {/* 8b. together with what? */}
<div className="max-w-[1140px] mx-auto mt-12 md:mt-0 mb-16 lg:mb-24">

  <div className="grid grid-cols-2 md:grid-cols-[1.15fr_1fr] gap-14 md:gap-12 items-center">

    {/* Left - Puzzle Image */}
<div className="flex flex-col items-center md:block md:justify-start md:mt-80">
  <img
    src="/Puzzle.png"
    alt=""
    loading="lazy"
    className="w-full max-w-[160px] sm:max-w-[220px] md:max-w-[500px] h-auto rounded-xl2"
  />
</div>

    {/* Right */}
    <div className="flex flex-col justify-center text-left md:text-right w-full h-full">

      {/* Desktop heading only */}
      <h2 className="hidden md:block relative -top-24 font-cormorant text-[clamp(30px,3.4vw,46px)] text-wink/85 m-0 mb-3 mt-10">
        together with what?
      </h2>

      <div className="hidden md:block h-px bg-wline mt-10 mb-9" />

      <div className="mr-0 md:mt-40 md:mr-60">
        <ul className="m-0 p-0 list-none font-wserif text-[15px] sm:text-[19px] md:text-[clamp(20px,2vw,28px)] text-wink/85 space-y-2 md:space-y-7">

          <li>Healthy habits</li>

          <img
            src="/upVector.png"
            alt=""
            className="w-8 md:w-20 ml-6 md:ml-auto"
          />

          <li>Daily routine</li>

          <img
            src="/downVector.png"
            alt=""
            className="w-8 md:w-20 ml-6 md:ml-auto"
          />

          <li>Choices you make</li>

        </ul>
      </div>

    </div>

  </div>

</div>

  {/* 8c. Closing quote */}
  {/* 8c. Closing quote */}
<div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-10 items-center max-w-[1140px] mx-auto">

  {/* Left - Quote */}
  <blockquote className="font-wserif text-[18px] sm:text-[20px] md:text-[clamp(26px,3vw,42px)] leading-[1.1] md:leading-[1.3] text-wink/85 mb-2 md:mb-0 max-w-[300px] md:max-w-none">
  &ldquo;Every healthy routine has its pieces. This is one of them.&rdquo;
</blockquote>

  {/* Right - Images */}
<div className="grid grid-cols-2 md:grid-cols-2 gap-4 md:gap-4 mt-2 md:mt-0">
  {['/home/lifestyle-1.jpg', '/home/lifestyle-2.jpg'].map((src) => (
    <img
      key={src}
      src={src}
      alt="Wellvia daily ritual"
      loading="lazy"
      className="w-full h-32 sm:h-40 md:h-[clamp(180px,20vw,260px)] object-cover rounded-xl2"
    />
  ))}
</div>

</div>
</section>
    ),
  };

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1] }}
      className="overflow-hidden"
    >
      <PageMeta
        title="Wellvia — Wellness Redefined"
        description="Clean-label wellness gummies crafted in small batches — immunity, gut health, sleep, beauty and daily multivitamins, designed for your everyday ritual."
        canonicalPath="/"
        image="/multi-gummies.png"
      />
      <JsonLd id="organization" data={buildOrganizationSchema({ logo: '/favicon.svg' })} />
      <JsonLd id="website" data={buildWebSiteSchema()} />
      {sections.map((s) => {
        const render = sectionRenderers[s.key];
        if (!render) return null;
        return <Fragment key={s.key}>{render((s.title || '').trim())}</Fragment>;
      })}
    </motion.div>
  );
}
