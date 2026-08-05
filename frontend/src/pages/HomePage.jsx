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
import {
  Children,
  Fragment,
  cloneElement,
  isValidElement,
  useEffect,
  useRef,
  useState,
} from 'react';
import { AnimatePresence } from "framer-motion";
import { cn, formatPrice } from '@/lib/utils';
import { useStorefrontConfigWithDefaults } from '@/features/storefront-config/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { useAddToCart } from '@/features/cart/hooks';
import { useAddedToCartModal } from '@/features/cart/addedModalStore.js';
import {
  useIsInWishlist,
  useAddToWishlist,
  useRemoveFromWishlist,
} from '@/features/wishlist/hooks';
import { toast } from '@/components/ui/Toaster.jsx';

/* ── Static content ── */

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
    quote: "Finally, a wellness routine I actually stick to. Tastes great and fits effortlessly into my day.",
    name: "Priya S.",
    stars: 5,
  },
  {
    quote: "Loved the packaging, the taste, and the results. Highly recommend!",
    name: "Rohan M.",
    stars: 4,
  },
  {
    quote: "The gummies are delicious and I've actually been consistent for the first time.",
    name: "Ananya R.",
    stars: 5,
  },
  {
    quote: "My sleep quality improved within a couple of weeks. Amazing experience.",
    name: "Vatsal G.",
    stars: 5,
  },
  {
    quote: "Simple, effective, and something I genuinely look forward to every day.",
    name: "Aarav P.",
    stars: 5,
  },
];

const AVATARS = [
  { id: 0, name: "Priya S.", image: "/female1.png" },
  { id: 1, name: "Rohan M.", image: "/male1.png" },
  { id: 2, name: "Ananya R.", image: "/femalee-2.png" },
  { id: 3, name: "Vatsal G.", image: "/male2.png" },
  { id: 4, name: "Aarav P.", image: "/male3.png" },
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

/**
 * One card in the bestsellers rail.
 *
 * `focused` is injected by AutoScrollRail for the card currently aligned to the
 * rail's snap point — the product the shopper just scrolled to. It lifts that
 * card forward and recesses the rest, so a rail that used to read as four
 * equally-weighted cards with one clipped at the edge now has an obvious
 * subject. Purely presentational: nothing about the card's behaviour changes.
 */
function BestsellerCard({ product, focused = false }) {
  const addToCart = useAddToCart();
  const showAdded = useAddedToCartModal((s) => s.showAdded);
  const inWishlist = useIsInWishlist(product.id);
  const addWishlist = useAddToWishlist();
  const removeWishlist = useRemoveFromWishlist();
  const isSignedIn = useAuthStore((s) => !!s.accessToken);
  const navigate = useNavigate();
  const location = useLocation();

  const { id, name, price, compare_at_price, image_url, stock } = product;
  const isDiscounted = compare_at_price != null && Number(compare_at_price) > Number(price);
  const outOfStock = stock <= 0;

  const handleWishlist = (e) => {
    e.preventDefault();
    e.stopPropagation();
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
        onSuccess: () => showAdded({ id, name, image_url, price, quantity: 1 }),
        onError: (err) =>
          toast.error(
            err?.response?.data?.error?.message ||
              'Could not add to cart. Please try again.',
          ),
      },
    );
  };

  return (
    <div
      // aria-current marks the focused card for assistive tech, which cannot
      // see the lift. The rail is a list of links, so "true" (rather than
      // "page"/"step") is the right token for "this is the current one".
      aria-current={focused ? 'true' : undefined}
      className={cn(
        'shrink-0 w-[60%] sm:w-[35%] md:w-[260px] lg:w-[280px] snap-start rounded-xl overflow-hidden flex flex-col',
        // origin-bottom so the lift grows upward into the rail's pt-3 headroom
        // instead of pushing down into the scrollbar track.
        'origin-bottom transition-[transform,opacity,box-shadow,border-color] duration-300 ease-out',
        // Kept modest on purpose: the rail's cross-axis overflow is `auto`, so a
        // large scale or a wide shadow spread would clip or, worse, add a
        // vertical scrollbar to a horizontal rail.
        focused
          ? 'border border-[#08112C]/25 bg-white shadow-[0_8px_20px_-10px_rgba(8,17,44,0.4)] scale-[1.02] opacity-100'
          : 'border border-[#EBE8E0] bg-[#FAF9F6] shadow-[0_2px_6px_rgba(0,0,0,0.02)] opacity-80',
        // Under prefers-reduced-motion the focus still reads through the border,
        // background and shadow — only the movement is dropped.
        'motion-reduce:transition-none motion-reduce:scale-100',
      )}
    >
      <div className="relative aspect-[4/3] md:aspect-[16/9] w-full bg-[#F3F2EE] flex items-center justify-center p-2.5">
        <Link to={`/products/${id}`} className="block w-full h-full">
          <WImage
            src={image_url}
            alt={name}
            className="w-full h-full object-contain max-h-[80%]"
          />
        </Link>

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

/**
 * AutoScrollRail — horizontal rail that advances one card every few seconds
 * and loops back to the start after the last one.
 *
 * Holds still while the shopper is on it (hover, focused card) and for a
 * grace period after any scroll input, so it never fights a browsing finger.
 * Only runs while the rail is actually on screen, and sits out entirely under
 * prefers-reduced-motion.
 */
const AUTO_SCROLL_MS = 3500;
const RESUME_AFTER_MS = 5000;

/**
 * Index of the card sitting on the rail's snap line — the focused product.
 *
 * @param cardLefts viewport x of each card's left edge, in DOM order
 * @param anchor    viewport x of the snap line (rail's left edge + scroll padding)
 *
 * Nearest edge wins, with the earlier card taking an exact tie, so a rail parked
 * exactly between two cards resolves left-to-right rather than flickering.
 * Extracted from the scroll handler because this is the whole decision — which
 * product is current — and it is worth asserting without a DOM.
 */
export function focusedCardIndex(cardLefts, anchor) {
  let best = 0;
  let bestGap = Infinity;
  cardLefts.forEach((left, i) => {
    const gap = Math.abs(left - anchor);
    if (gap < bestGap) {
      bestGap = gap;
      best = i;
    }
  });
  return best;
}

function AutoScrollRail({ className, children }) {
  const railRef = useRef(null);
  const reduce = useReducedMotion();
  const [activeIndex, setActiveIndex] = useState(0);
  const cardCount = Children.count(children);

  // ── Which card is in focus ──────────────────────────────────────────────────
  // The rail is `snap-x snap-mandatory` with `snap-start` cards, so a scroll
  // always settles with one card aligned to the scrollport's start edge. That
  // card is the focused one: it is the product the shopper just scrolled to, and
  // picking it by measurement rather than by counting pixels means it stays
  // correct across the four card widths (60% / 35% / 260px / 280px) without
  // knowing any of them.
  //
  // Runs regardless of prefers-reduced-motion — this is which card is current,
  // not an animation. The card suppresses its own transform under that setting.
  useEffect(() => {
    const rail = railRef.current;
    if (!rail) return undefined;

    let frame = 0;
    const measure = () => {
      frame = 0;
      const cards = Array.from(rail.children);
      if (!cards.length) return;
      // The snap line, not the box edge: the rail carries horizontal padding on
      // mobile (`px-5`), and `scroll-pl-*` moves the snap position in by the
      // same amount. Measuring against the raw left edge would report the
      // previous card as focused for the width of that padding.
      const padding = parseFloat(getComputedStyle(rail).scrollPaddingLeft) || 0;
      const anchor = rail.getBoundingClientRect().left + padding;
      const next = focusedCardIndex(
        cards.map((card) => card.getBoundingClientRect().left),
        anchor,
      );
      setActiveIndex((prev) => (prev === next ? prev : next));
    };

    // Coalesced to one measurement per frame: a scroll fires far more often
    // than the browser paints, and each pass reads layout for every card.
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure);
    };

    measure();
    rail.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    return () => {
      if (frame) cancelAnimationFrame(frame);
      rail.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
    };
    // Card COUNT, not `children`: the children array is a fresh identity on
    // every HomePage render, which would tear down and re-attach the listener
    // for no reason. Only a change in how many cards exist needs a re-measure.
  }, [cardCount]);

  useEffect(() => {
    const rail = railRef.current;
    if (!rail || reduce) return undefined;

    let hovered = false;
    let focused = false;
    let visible = false;
    let lastInput = 0;

    const markInput = () => { lastInput = Date.now(); };
    const onEnter = () => { hovered = true; };
    const onLeave = () => { hovered = false; };
    const onFocusIn = () => { focused = true; };
    const onFocusOut = () => { focused = false; };

    const observer = new IntersectionObserver(
      ([entry]) => { visible = entry.isIntersecting; },
      { threshold: 0.4 },
    );
    observer.observe(rail);

    rail.addEventListener('pointerenter', onEnter);
    rail.addEventListener('pointerleave', onLeave);
    rail.addEventListener('focusin', onFocusIn);
    rail.addEventListener('focusout', onFocusOut);
    rail.addEventListener('pointerdown', markInput);
    rail.addEventListener('wheel', markInput, { passive: true });
    rail.addEventListener('touchstart', markInput, { passive: true });
    rail.addEventListener('touchmove', markInput, { passive: true });

    const timer = setInterval(() => {
      if (hovered || focused || !visible) return;
      if (Date.now() - lastInput < RESUME_AFTER_MS) return;
      const cards = rail.children;
      if (cards.length < 2) return;
      // Card width + flex gap, measured rather than hardcoded per breakpoint.
      const step = cards[1].offsetLeft - cards[0].offsetLeft;
      const atEnd =
        rail.scrollLeft + rail.clientWidth >= rail.scrollWidth - step / 2;
      if (atEnd) rail.scrollTo({ left: 0, behavior: 'smooth' });
      else rail.scrollBy({ left: step, behavior: 'smooth' });
    }, AUTO_SCROLL_MS);

    return () => {
      clearInterval(timer);
      observer.disconnect();
      rail.removeEventListener('pointerenter', onEnter);
      rail.removeEventListener('pointerleave', onLeave);
      rail.removeEventListener('focusin', onFocusIn);
      rail.removeEventListener('focusout', onFocusOut);
      rail.removeEventListener('pointerdown', markInput);
      rail.removeEventListener('wheel', markInput);
      rail.removeEventListener('touchstart', markInput);
      rail.removeEventListener('touchmove', markInput);
    };
  }, [reduce]);

  return (
    <div ref={railRef} className={className}>
      {/* `focused` is injected rather than passed by the caller: only the rail
          knows its own scroll position, and the caller renders the cards from a
          plain .map(). A child that does not accept the prop simply ignores it. */}
      {Children.map(children, (child, i) =>
        isValidElement(child) ? cloneElement(child, { focused: i === activeIndex }) : child,
      )}
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

  const {
    data: bestsellersData,
    isLoading: bestsellersLoading,
    isError: bestsellersError,
    refetch: refetchBestsellers,
  } = useBestsellers(12);
  const bestsellers = bestsellersData ?? [];

  const { data: combosData } = useProducts({
    is_combo: true,
    page_size: 8,
  });
  const combos = combosData?.items ?? [];

  const { config } = useStorefrontConfigWithDefaults();
  const sections = (config.homepage_sections ?? []).filter(
    (s) => s.visible !== false,
  );
  const whyWeExistVisible = sections.some((s) => s.key === 'why-we-exist');

  const sectionRenderers = {
    hero: () => <HeroSection showWhyButton={whyWeExistVisible} />,

    'why-we-exist': (title) => (
      <section
        id="why-we-exist"
        className="grid grid-cols-[1.7fr_1fr] md:grid-cols-[1.2fr_1fr] gap-2 sm:gap-6 lg:gap-12 items-center md:items-start pl-0 pr-3 sm:pr-10 lg:pr-16 py-6 lg:py-[40px]"
      >
        <div className="flex flex-col items-start w-full">
          <img
            src="/gummy3.png"
            alt=""
            aria-hidden="true"
            className="w-[105px] sm:w-[130px] md:w-[210px] lg:w-[250px] -mb-12 md:-mb-28 lg:-mb-32 ml-2 sm:ml-16 md:ml-20 lg:ml-24 relative z-10"
          />
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
            /* scroll-pl-5 md:scroll-pl-0 mirrors px-5 md:px-0: without it
               snap-start aligns cards to the raw scrollport edge, parking the
               focused card underneath the rail's own padding.
               pt-3 is headroom for the focused card's lift — the cross-axis
               overflow is `auto`, so a card growing past the top edge would be
               clipped and could raise a vertical scrollbar. */
            <AutoScrollRail className="flex gap-3 md:gap-4 lg:gap-6 overflow-x-auto snap-x snap-mandatory scroll-pl-5 md:scroll-pl-0 -mx-5 px-5 md:mx-auto md:px-0 pt-3 pb-4 max-w-[1080px] lg:max-w-[1280px] [&::-webkit-scrollbar]:h-2 [&::-webkit-scrollbar-thumb]:bg-gray-300 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-track]:bg-transparent">
              {bestsellers.map((p) => (
                <BestsellerCard key={p.id} product={p} />
              ))}
            </AutoScrollRail>
          )}
        </section>
      ),

    'new-launches': (title) => (
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <div className="max-w-[1100px] mx-auto flex flex-col items-center">
          <div className="flex flex-col items-center justify-center mb-8 md:mb-12 text-center w-full max-w-[600px]">
            <h2 className="font-wserif text-[24px] sm:text-[32px] md:text-[clamp(32px,3.5vw,48px)] text-wink/85 m-0 mb-3 md:mb-4 leading-tight">
              {title || 'New Launches'}
            </h2>
            <Link
              to="/products"
              className="hidden md:inline-flex items-center gap-1.5 md:gap-2 bg-[#08112C] text-white rounded-full px-5 py-2 md:px-7 md:py-2.5 text-[13px] md:text-[16px] font-wserif hover:opacity-90 transition-opacity"
            >
              Explore all
              <span className="text-[13px] md:text-[16px]">↗</span>
            </Link>
          </div>

          <div className="w-full grid grid-cols-1 md:grid-cols-2 gap-8 md:gap-12 lg:gap-16 items-center justify-items-center">
            <div className="order-2 md:order-1 flex items-center justify-center relative isolate w-full">
              <img
                src="/launches-bg.png"
                alt=""
                aria-hidden="true"
                className="absolute z-0 w-[110%] max-w-none h-auto top-[78%] left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none opacity-80 md:w-[150%] md:left-[48%] md:origin-center"
              />
              <img
                src="/launches.png"
                alt="Core Omega, Beauty Boost and Sleep gummies — the limited collection"
                loading="lazy"
                className="w-full max-w-[520px] h-auto rounded-xl2 relative z-10 mx-auto"
              />
            </div>

            <div className="order-1 md:order-2 flex flex-row items-center justify-between gap-3 md:flex-col md:items-start md:justify-center w-full">
              <h3 className="font-wserif font-semibold text-[16px] sm:text-[20px] md:text-[clamp(26px,2.8vw,38px)] leading-[1.15] text-wgreen-deep m-0 md:mb-7 max-w-[60%] md:max-w-none">
                The Wellness Gummy
                <br className="hidden md:block" />{' '}
                Limited Collection
              </h3>
              <Link
                to="/new-arrivals"
                className="shrink-0 inline-block bg-transparent border border-[#08112C] text-[#08112C] no-underline rounded-[10px] md:rounded-[12px] px-4 py-2 md:px-10 md:py-3.5 font-wserif text-[13px] md:text-[18px] tracking-wide hover:bg-[#010E37] hover:text-white transition-colors whitespace-nowrap"
              >
                Shop Now!
              </Link>
            </div>
          </div>
        </div>
      </section>
    ),

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
              src={`/routine-${r.key}.png`}
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
            <article
              key={post.title}
              className="shrink-0 w-[70%] sm:w-auto snap-start"
            >
              <img
                src={post.image}
                alt=""
                loading="lazy"
                className="w-full h-[260px] sm:h-[300px] lg:h-[380px] xl:h-[420px] object-cover rounded-xl2 mb-3 sm:mb-5"
              />
              <h3 className="font-wserif font-semibold text-[15px] sm:text-[clamp(20px,1.9vw,26px)] leading-[1.25] text-wink m-0 mb-1 sm:mb-1.5">
                {post.title}
              </h3>
              <div className="font-wserif text-[11px] sm:text-[14.5px] text-wmuted mb-1.5 sm:mb-2">
                {post.date}
              </div>
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

    reviews: (title) => (
      <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
        <h2 className="font-wserif font-semibold text-[clamp(28px,3.4vw,44px)] text-wink text-center m-0 mb-10">
          {title || 'The Reviews Behind the Routine'}
        </h2>

        <div className="max-w-[1080px] mx-auto border border-wgreen/40 rounded-xl3 bg-wcard/40 p-3 sm:p-6 md:p-10 grid grid-cols-[0.95fr_1.05fr] md:grid-cols-[1fr_1.25fr] gap-3 sm:gap-6 md:gap-8 items-center">
          <div className="flex items-center justify-center" aria-hidden="true">
            <div className="relative aspect-square w-full max-w-[140px] sm:max-w-[180px] md:max-w-[340px] rounded-xl2">
              {AVATARS.map((avatar, index) => {
                const positions = [
                  'top-[10%] left-[8%] w-[22%]',
                  'top-[25%] right-[8%] w-[24%]',
                  'top-[42%] left-[34%] w-[18%]',
                  'bottom-[8%] left-[8%] w-[22%]',
                  'bottom-[8%] right-[12%] w-[22%]',
                ];

                const isActive = index === reviewIndex;
                const isFemalee2 = index === 2;

                return (
                  <img
                    key={avatar.id}
                    src={avatar.image}
                    alt={avatar.name}
                    className={`absolute rounded-full aspect-square object-cover transition-all duration-500 ${positions[index]} ${
                      isFemalee2 ? 'scale-[1.3] object-center' : ''
                    }`}
                    style={{
                      boxShadow: isActive ? "0 0 16px 8px rgba(135, 182, 169, 0.6)" : "none",
                      border: isActive ? "3px solid #87B6A9" : "3px solid transparent",
                      outline: "none",
                      zIndex: isActive ? 10 : 1,
                      opacity: isActive ? 1 : 0.85,
                    }}
                  />
                );
              })}
            </div>
          </div>

          <div className="relative bg-white rounded-xl2 shadow-[0_28px_60px_-34px_rgba(30,30,26,0.45)] px-3 py-3 sm:px-6 sm:pt-10 sm:pb-1 md:px-9 md:pt-14 md:pb-2">
            <img 
              src="/up-quote.png" 
              alt="" 
              aria-hidden="true"
              className="absolute top-2 right-3 sm:top-5 sm:right-7 w-6 sm:w-8 md:w-10 h-auto pointer-events-none select-none"
            />

            <AnimatePresence mode="wait">
              <motion.div
                key={reviewIndex}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                transition={{ duration: 0.7 }}
              >
                <Stars count={REVIEWS[reviewIndex].stars} className="text-[11px] sm:text-[18px] md:text-[22px]" />
                <blockquote className="font-wserif font-medium text-[11px] sm:text-[17px] md:text-[clamp(18px,1.8vw,23px)] leading-[1.25] sm:leading-[1.3] md:leading-[1.5] text-wink m-0 my-0.5 sm:mt-4 sm:mb-5">
                  “{REVIEWS[reviewIndex].quote}”
                </blockquote>
                <div className="font-wserif text-[10px] sm:text-[15px] md:text-[17px] text-wink/80">
                  {REVIEWS[reviewIndex].name}
                </div>
              </motion.div>
            </AnimatePresence>

            <div className="flex justify-start mt-1 sm:mt-4">
              <img 
                src="/down-quote.png" 
                alt="" 
                aria-hidden="true"
                className="w-7 sm:w-9 md:w-20 lg:w-24 h-auto opacity-40 pointer-events-none select-none"
              />
            </div>
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

    'brand-philosophy': (title) => (
      <section className="px-5 sm:px-10 lg:px-16 pt-2 pb-12 lg:pt-8 lg:pb-[60px]">
        <div className="grid grid-cols-2 md:grid-cols-2 gap-4 md:gap-10 items-center max-w-[1140px] mx-auto mb-2 md:mb-6 lg:mb-8">
          <div className="pl-0 md:pl-16 lg:pl-24">
            <h2 className="font-wserif text-[18px] sm:text-[26px] md:text-[clamp(32px,3.8vw,52px)] leading-[1.2] md:leading-[1.15] text-wink/85 m-0 mb-2 md:mb-5">
              {title || (
                <>
                  the piece that brings
                  <br />
                  it all together.
                </>
              )}
            </h2>
            <p className="font-wserif text-[15px] sm:text-[18px] md:text-[clamp(20px,1.9vw,26px)] leading-[1.4] md:leading-[1.5] text-wmuted m-0 max-w-[340px] md:max-w-[420px] mb-4">
              &ldquo;sometimes, the smallest things make the biggest difference.&rdquo;
            </p>
            <div className="h-px bg-wline w-full max-w-[340px] md:max-w-[420px] my-3" />
          </div>

          <div className="flex justify-center" aria-hidden="true">
            <div className="relative flex items-center justify-center p-2 sm:p-4">
              <img
                src="/huge-puzzle bg.png"
                alt=""
                aria-hidden="true"
                className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[85%] max-w-[130px] sm:max-w-[210px] md:max-w-[320px] h-auto object-contain pointer-events-none select-none z-0"
              />
              <img
                src="/huge-puzzle.png"
                alt=""
                loading="lazy"
                className="relative z-10 w-full max-w-[160px] sm:max-w-[260px] md:max-w-[400px] h-auto rounded-xl2"
              />
            </div>
          </div>
        </div>

        <div className="flex justify-center items-center gap-4 sm:gap-8 md:gap-16 mt-0">
          <img
            src="/homepage download.png"
            alt=""
            className="w-24 sm:w-32 md:w-64 translate-y-4 md:translate-y-16 lg:translate-y-20 md:-translate-x-16 lg:-translate-x-20"
          />

          <div className="text-center md:text-left z-10 flex flex-col items-center md:items-start md:-mt-36 lg:-mt-48">
            <p className="font-cormorant text-[13px] sm:text-[14px] md:text-[22px] leading-[1.3] md:leading-snug text-wink mb-2 md:mb-2 max-w-[600px] md:max-w-[200px] text-center md:text-left">
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
              className="w-16 sm:w-24 md:w-52 translate-y-0 md:translate-y-16 lg:translate-y-20"
            />
            <h2 className="block md:hidden font-cormorant text-[15px] sm:text-[18px] text-wink/85 text-center -mt-2 sm:mt-0 p-0 leading-tight mb-2 whitespace-nowrap">
              together with what?
            </h2>
            <div className="block md:hidden h-px bg-wline w-28 mb-6" />
          </div>
        </div>

        <div className="max-w-[1140px] mx-auto mt-4 md:mt-4 mb-10 lg:mb-16">
          <div className="flex flex-row md:grid md:grid-cols-[1fr_auto] justify-center items-center gap-3 sm:gap-6 md:gap-8 max-w-[420px] sm:max-w-none mx-auto pr-0 md:pr-4">
            <div className="flex flex-col items-center justify-center md:block md:justify-start md:mt-12 shrink-0">
              <img
                src="/Puzzle.png"
                alt=""
                loading="lazy"
                className="w-full max-w-[240px] sm:max-w-[280px] md:max-w-[620px] h-auto rounded-xl2"
              />
            </div>

            <div className="flex flex-col justify-start text-left w-full h-full md:pl-4">
              <h2 className="hidden md:block font-cormorant text-[clamp(30px,3.4vw,46px)] text-wink/85 m-0 mb-3 mt-0">
                together with what?
              </h2>
              <div className="hidden md:block h-px bg-wline mt-3 mb-12 lg:mb-16" />

              <div className="md:mt-16 lg:mt-20">
                <ul className="m-0 p-0 list-none font-wserif text-[13px] sm:text-[19px] md:text-[clamp(20px,2vw,28px)] text-wink/85 space-y-1.5 md:space-y-7">
                  <li>Healthy habits</li>
                  <img
                    src="/upVector.png"
                    alt=""
                    className="w-6 sm:w-8 md:w-20 ml-2 md:ml-12"
                  />
                  <li>Daily routine</li>
                  <img
                    src="/downVector.png"
                    alt=""
                    className="w-6 sm:w-8 md:w-20 ml-2 md:ml-12"
                  />
                  <li>Choices you make</li>
                </ul>
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 md:gap-10 items-center max-w-[1140px] mx-auto">
          <blockquote className="font-wserif text-[18px] sm:text-[20px] md:text-[clamp(26px,3vw,42px)] leading-[1.1] md:leading-[1.3] text-wink/85 mb-2 md:mb-0 max-w-[300px] md:max-w-none">
            &ldquo;Every healthy routine has its pieces.<br className="block md:hidden" /> This is one of them.&rdquo;
          </blockquote>

          <div className="grid grid-cols-2 md:grid-cols-2 gap-4 md:gap-4 mt-2 md:mt-0">
            {['/eat.png', '/home/lifestyle-2.jpg'].map((src) => (
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