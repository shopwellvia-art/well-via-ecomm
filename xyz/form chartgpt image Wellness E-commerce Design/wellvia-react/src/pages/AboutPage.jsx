import ImageSlot from '../components/ImageSlot';
import Footer from '../components/Footer';

const STATS = [
  { value: '12.4k+', label: 'Five-star reviews' },
  { value: '100%', label: 'Clean ingredients' },
  { value: 'GMP', label: 'Certified' },
  { value: 'FSSAI', label: 'Compliant' },
];
const VALUES = [
  { title: 'Clinically backed', desc: 'Formulas grounded in published research and real outcomes.' },
  { title: 'Radically clean', desc: 'No refined sugar, no fillers, fully transparent labels.' },
  { title: 'Beautifully simple', desc: 'Rituals that fit effortlessly into modern life.' },
];

export default function AboutPage() {
  return (
    <>
      <section className="grid md:grid-cols-2 gap-6 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 pt-9 lg:pt-[72px] pb-9">
        <div>
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-4">Our Story</div>
          <h1 className="font-serif font-medium text-[clamp(38px,5vw,64px)] leading-[1.02] m-0 mb-5">Wellness, made beautifully simple</h1>
          <p className="text-[15.5px] leading-[1.75] text-muted m-0 mb-4 font-light">Wellvia began with a simple belief: daily nutrition should feel like a ritual you look forward to — not a chore. We blend clinically-backed ingredients with clean, transparent formulations.</p>
          <p className="text-[15.5px] leading-[1.75] text-muted m-0 font-light">Every product is FSSAI compliant, GMP certified, and crafted for modern lives.</p>
        </div>
        <ImageSlot id="about-hero" shape="rounded" radius={22} placeholder="Drop brand lifestyle image" className="w-full h-[clamp(320px,40vw,460px)]" />
      </section>

      <section className="px-5 sm:px-10 lg:px-16 py-8 lg:py-[52px] bg-green text-[#f3efe6]">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-5 lg:gap-10 max-w-[1000px] mx-auto text-center">
          {STATS.map((s) => (
            <div key={s.label}>
              <div className="font-serif text-[clamp(34px,4vw,48px)] text-white leading-none mb-2">{s.value}</div>
              <div className="text-[12px] tracking-wide uppercase text-[#f3efe6]/70">{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="px-5 sm:px-10 lg:px-16 py-9 lg:py-16">
        <h2 className="font-serif font-medium text-[clamp(28px,3.4vw,42px)] text-center m-0 mb-7 lg:mb-11">What we stand for</h2>
        <div className="grid md:grid-cols-3 gap-[18px] max-w-[1080px] mx-auto">
          {VALUES.map((v) => (
            <div key={v.title} className="bg-card border border-line rounded-xl2 p-[26px]">
              <div className="font-serif text-[22px] mb-2">{v.title}</div>
              <div className="text-[13.5px] text-muted leading-[1.6] font-light">{v.desc}</div>
            </div>
          ))}
        </div>
      </section>

      <Footer />
    </>
  );
}
