import { Link } from 'react-router-dom';
import ImageSlot from './ImageSlot';
import TrustBadges from './TrustBadges';

export default function HeroSection() {
  return (
    <section className="grid md:grid-cols-[1.05fr_1fr] gap-6 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 pt-9 lg:pt-[72px] pb-10 lg:pb-16">
      <div>
        <div className="inline-flex items-center gap-2 text-[11px] tracking-[0.22em] uppercase text-gold mb-[22px]">
          <span className="w-6 h-px bg-gold" />
          Clinically-backed wellness
        </div>
        <h1 className="font-serif font-medium text-[clamp(42px,5.6vw,72px)] leading-[1.02] -tracking-[0.01em] m-0 mb-[22px] text-ink">
          Wellness, Refined
          <br />
          for Everyday Living
        </h1>
        <p className="text-[clamp(15px,1.3vw,17.5px)] leading-[1.7] text-muted max-w-[430px] m-0 mb-8 font-light">
          Clinically backed nutrition and daily wellness rituals designed to feel gentle,
          effective, and beautifully simple.
        </p>
        <div className="flex flex-wrap gap-3.5 mb-9">
          <Link
            to="/shop"
            className="bg-green text-white no-underline rounded-full px-[34px] py-4 text-[14px] tracking-wide cursor-pointer shadow-[0_12px_28px_-12px_rgba(24,58,46,0.7)] hover:bg-greenh"
          >
            Shop Rituals
          </Link>
          <Link
            to="/about"
            className="bg-transparent text-ink no-underline border border-line rounded-full px-8 py-4 text-[14px] tracking-wide cursor-pointer hover:border-green"
          >
            Why Wellvia?
          </Link>
        </div>
        <TrustBadges />
      </div>

      <div className="relative">
        <div className="absolute -right-[6%] -bottom-[8%] w-[62%] h-[78%] z-0 rounded-full"
          style={{ background: 'radial-gradient(circle at 50% 40%, rgba(180,154,99,0.16), transparent 70%)' }} />
        <ImageSlot
          id="hero"
          shape="rounded"
          radius={22}
          placeholder="Drop hero product / lifestyle photo"
          className="w-full h-[clamp(360px,42vw,520px)] relative z-[1]"
        />
        <div className="absolute -left-3.5 bottom-6 z-[2] bg-card border border-line rounded-2xl px-[17px] py-[13px] shadow-[0_18px_40px_-20px_rgba(40,30,10,0.4)] flex items-center gap-3">
          <span className="font-serif text-[30px] text-green leading-none">4.8</span>
          <div className="text-[11px] leading-[1.5] text-muted">
            <span className="text-gold tracking-[1px]">★★★★★</span>
            <br />
            12,400+ reviews
          </div>
        </div>
      </div>
    </section>
  );
}
