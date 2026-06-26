import { Link } from 'react-router-dom';
import { PRODUCTS, REVIEWS } from '../data/products';
import HeroSection from '../components/HeroSection';
import ProductGrid from '../components/ProductGrid';
import ImageSlot from '../components/ImageSlot';
import Footer from '../components/Footer';
import { Check } from '../components/Icons';

const STORY_POINTS = ['Science-led formulations', 'Designed for daily use', 'Made for modern lifestyles'];
const TRUST_STATS = [
  { value: '12.4k+', label: 'Five-star reviews' },
  { value: '100%', label: 'Clean ingredients' },
  { value: 'GMP', label: 'Certified manufacturing' },
  { value: 'FSSAI', label: 'Approved & compliant' },
];

export default function HomePage() {
  const featured = PRODUCTS.slice(0, 4);

  return (
    <>
      <HeroSection />

      {/* Featured rituals */}
      <section className="px-5 sm:px-10 lg:px-16 py-10 lg:py-[68px] bg-gradient-to-b from-transparent to-[rgba(216,208,196,0.18)]">
        <div className="text-center mb-7 lg:mb-11">
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Curated for you</div>
          <h2 className="font-serif font-medium text-[clamp(30px,3.6vw,46px)] m-0 text-ink">Your Daily Wellness Rituals</h2>
        </div>
        <div className="max-w-[1140px] mx-auto">
          <ProductGrid products={featured} />
        </div>
        <div className="text-center mt-[34px]">
          <Link to="/shop" className="inline-block bg-transparent border border-green text-green no-underline rounded-full px-[34px] py-[13px] text-[13px] tracking-wide cursor-pointer hover:bg-green hover:text-white">
            View All Products
          </Link>
        </div>
      </section>

      {/* Brand story */}
      <section className="grid md:grid-cols-2 gap-6 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 py-10 lg:py-[72px]">
        <div>
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-4">Our Story</div>
          <h2 className="font-serif font-medium text-[clamp(30px,3.8vw,48px)] leading-[1.08] m-0 mb-5">Wellness that<br />fits into real life</h2>
          <p className="text-[15.5px] leading-[1.75] text-muted m-0 mb-4 font-light max-w-[460px]">
            At Wellvia, we believe nutrition should feel light, enjoyable, and trustworthy — never
            clinical or overwhelming. Every formulation is thoughtfully crafted with clean
            ingredients, transparent labels and science-led outcomes.
          </p>
          <div className="flex flex-col gap-3 mt-6">
            {STORY_POINTS.map((s) => (
              <div key={s} className="flex items-center gap-3 text-[14.5px] text-ink">
                <span className="w-[30px] h-[30px] rounded-full border border-line flex items-center justify-center shrink-0"><Check size={14} stroke="#183A2E" /></span>
                {s}
              </div>
            ))}
          </div>
        </div>
        <ImageSlot id="story" shape="rounded" radius={22} placeholder="Drop a calm lifestyle photo" className="w-full h-[clamp(340px,40vw,480px)]" />
      </section>

      {/* Trust stats */}
      <section className="px-5 sm:px-10 lg:px-16 py-8 lg:py-[52px] bg-green text-[#f3efe6]">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-5 lg:gap-10 max-w-[1100px] mx-auto text-center">
          {TRUST_STATS.map((t) => (
            <div key={t.label}>
              <div className="font-serif text-[clamp(34px,4vw,48px)] text-white leading-none mb-2">{t.value}</div>
              <div className="text-[12px] tracking-wide text-[#f3efe6]/70 uppercase">{t.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Reviews */}
      <section className="px-5 sm:px-10 lg:px-16 py-10 lg:py-[68px]">
        <div className="text-center mb-7 lg:mb-11">
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Loved by thousands</div>
          <h2 className="font-serif font-medium text-[clamp(30px,3.6vw,46px)] m-0">Real Rituals, Real Results</h2>
        </div>
        <div className="grid md:grid-cols-3 gap-[18px] max-w-[1100px] mx-auto">
          {REVIEWS.map((r, i) => (
            <div key={i} className="bg-card border border-line rounded-xl2 p-6 flex flex-col gap-3.5">
              <span className="text-gold tracking-[2px] text-[14px]">★★★★★</span>
              <p className="font-serif text-[18px] leading-[1.5] text-ink m-0 flex-1 italic">“{r.quote}”</p>
              <div className="flex items-center gap-2.5">
                <ImageSlot id={`rev-${i}`} shape="circle" className="w-10 h-10 shrink-0" />
                <div className="text-[13px]">
                  <div className="text-ink">{r.name}</div>
                  <div className="text-muted text-[11.5px]">{r.role}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <Footer />
    </>
  );
}
