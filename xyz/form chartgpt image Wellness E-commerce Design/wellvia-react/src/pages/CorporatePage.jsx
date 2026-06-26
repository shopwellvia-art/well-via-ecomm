import { Link } from 'react-router-dom';
import ImageSlot from '../components/ImageSlot';
import Footer from '../components/Footer';

const FEATURES = [
  { title: 'Curated gift boxes', desc: 'Premium, fully customisable wellness hampers.' },
  { title: 'Volume pricing', desc: 'Tiered rates for teams of any size.' },
  { title: 'Managed delivery', desc: 'Pan-India logistics, handled end to end.' },
];

export default function CorporatePage() {
  return (
    <>
      <section className="grid md:grid-cols-2 gap-6 lg:gap-14 items-center px-5 sm:px-10 lg:px-16 pt-9 lg:pt-[68px] pb-9">
        <div>
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-4">Corporate &amp; Gifting</div>
          <h1 className="font-serif font-medium text-[clamp(34px,4.4vw,56px)] leading-[1.04] m-0 mb-[18px]">Wellness for your whole team</h1>
          <p className="text-[15.5px] leading-[1.75] text-muted m-0 mb-6 font-light">Curated corporate gift boxes and employee wellness programs — clean, premium and effortlessly delivered at scale.</p>
          <div className="flex gap-3.5 flex-wrap">
            <Link to="/contact" className="bg-green text-white no-underline rounded-full px-[30px] py-[15px] text-[14px] cursor-pointer hover:bg-greenh">Request a Quote</Link>
            <button className="bg-transparent border border-line rounded-full px-[30px] py-[15px] text-[14px] cursor-pointer">Download Catalogue</button>
          </div>
        </div>
        <ImageSlot id="corp-hero" shape="rounded" radius={22} placeholder="Drop corporate gifting image" className="w-full h-[clamp(300px,38vw,440px)]" />
      </section>

      <section className="px-5 sm:px-10 lg:px-16 py-8 lg:py-14">
        <div className="grid md:grid-cols-3 gap-[18px] max-w-[1080px] mx-auto">
          {FEATURES.map((c) => (
            <div key={c.title} className="bg-card border border-line rounded-xl2 p-[26px]">
              <div className="font-serif text-[22px] mb-2">{c.title}</div>
              <div className="text-[13.5px] text-muted leading-[1.6] font-light">{c.desc}</div>
            </div>
          ))}
        </div>
      </section>

      <Footer />
    </>
  );
}
