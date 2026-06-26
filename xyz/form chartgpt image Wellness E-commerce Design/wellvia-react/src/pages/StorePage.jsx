import ImageSlot from '../components/ImageSlot';
import Footer from '../components/Footer';

const STORES = [
  { city: 'Mumbai', address: 'Shop 4, Pali Hill, Bandra West, 400050', hours: 'Open daily · 10am–9pm', slot: 'store-1' },
  { city: 'Delhi', address: 'M-12, Khan Market, New Delhi, 110003', hours: 'Open daily · 10am–9pm', slot: 'store-2' },
  { city: 'Bangalore', address: '112, Indiranagar 100ft Rd, 560038', hours: 'Open daily · 10am–9pm', slot: 'store-3' },
];

export default function StorePage() {
  return (
    <>
      <section className="text-center px-5 sm:px-10 lg:px-16 pt-10 lg:pt-[68px] pb-6 lg:pb-9">
        <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Find Us</div>
        <h1 className="font-serif font-medium text-[clamp(36px,4.6vw,58px)] m-0 mb-3">Wellvia stores</h1>
        <p className="text-[15px] text-muted max-w-[520px] mx-auto font-light">Experience our rituals in person across India.</p>
      </section>

      <section className="px-5 sm:px-10 lg:px-16 pb-10 lg:pb-16">
        <div className="max-w-[1080px] mx-auto grid md:grid-cols-3 gap-4">
          {STORES.map((s) => (
            <div key={s.city} className="bg-card border border-line rounded-xl2 overflow-hidden">
              <ImageSlot id={s.slot} placeholder="Drop store photo" className="w-full h-[150px]" />
              <div className="p-5">
                <div className="font-serif text-[21px] mb-1.5">{s.city}</div>
                <div className="text-[13px] text-muted leading-[1.6] font-light">{s.address}</div>
                <div className="text-[12.5px] text-green mt-2.5">{s.hours}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <Footer />
    </>
  );
}
