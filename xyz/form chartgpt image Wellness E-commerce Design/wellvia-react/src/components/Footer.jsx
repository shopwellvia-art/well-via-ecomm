import { Link } from 'react-router-dom';
import { LeafMark } from './Logo';
import ImageSlot from './ImageSlot';
import { TruckIcon, ShieldIcon, SupportIcon, LockIcon } from './Icons';

const COLS = [
  { title: 'Wellvia', links: [['Shop All', '/shop'], ['Rituals', '/shop'], ['Our Story', '/about']] },
  { title: 'Customer Service', links: [['Contact', '/contact'], ['Press', '/press'], ['Careers', '/careers']] },
  { title: 'Account', links: [['Account', '/account'], ['Orders', '/account/orders'], ['Rewards', '/account/rewards']] },
];

export default function Footer() {
  return (
    <footer className="relative overflow-hidden border-t border-line px-5 sm:px-10 lg:px-14 pt-10 lg:pt-14 pb-7">
      <div className="pointer-events-none absolute top-0 inset-x-0 h-[120px] bg-gradient-to-b from-[rgba(206,190,206,0.35)] to-transparent z-0" />
      <ImageSlot id="foot-left" placeholder="botanical" className="absolute -left-2.5 -bottom-2.5 w-[clamp(110px,14vw,180px)] h-[150px] z-0" />
      <ImageSlot id="foot-right" placeholder="botanical" className="absolute -right-2.5 -bottom-2.5 w-[clamp(120px,15vw,200px)] h-[160px] z-0" />

      <div className="relative z-[1] max-w-[1120px] mx-auto">
        {/* Logo */}
        <div className="flex flex-col items-center gap-1 mb-7 lg:mb-9">
          <LeafMark size={48} />
          <div className="font-display tracking-[0.28em] text-[clamp(26px,3.2vw,34px)] font-medium text-green pl-[0.28em]">WELLVIA</div>
        </div>
        <div className="h-px bg-line mb-7 lg:mb-9" />

        <div className="flex flex-wrap gap-6 lg:gap-11 justify-between items-start">
          {/* Columns + newsletter */}
          <div className="flex-1 basis-[440px] min-w-[280px]">
            <div className="flex flex-wrap">
              {COLS.map((c, i) => (
                <div key={i} className={`flex-1 min-w-[130px] px-6 ${i === 0 ? 'pl-0' : 'border-l border-line'}`}>
                  <div className="font-serif font-semibold text-[19px] mb-4">{c.title}</div>
                  <div className="flex flex-col gap-2.5 text-[13.5px] text-muted">
                    {c.links.map(([label, to], j) => (
                      <Link key={j} to={to} className="hover:text-green no-underline text-muted w-fit">{label}</Link>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            <div className="flex items-center gap-3.5 mt-7 lg:mt-9">
              <svg width="40" height="22" viewBox="0 0 48 24" fill="none" className="shrink-0" aria-hidden>
                <path d="M8 16c-3-1-5-4-5-8 4 0 7 2 8 6" stroke="#B49A63" strokeWidth="1.2" />
                <path d="M20 14c-3-1-5-4-5-8 4 0 7 2 8 6" stroke="#B49A63" strokeWidth="1.2" />
                <path d="M32 16c-3-1-5-4-5-8 4 0 7 2 8 6" stroke="#B49A63" strokeWidth="1.2" />
              </svg>
              <div className="flex items-center border border-line rounded-full bg-card flex-1 max-w-[430px] p-1">
                <input placeholder="Enter your email address" className="flex-1 border-0 bg-transparent px-[18px] py-2.5 text-[13.5px] outline-none text-ink" />
                <button className="bg-green text-white border-0 px-6 py-2.5 text-[13px] cursor-pointer rounded-full whitespace-nowrap hover:bg-greenh">Join Now</button>
              </div>
            </div>
            <div className="flex items-center gap-2 mt-3.5 text-[12.5px] text-muted">
              <LockIcon size={13} stroke="#B49A63" />
              Checkout is encrypted &amp; secure.
            </div>
          </div>

          {/* Mini cart card */}
          <div className="flex-[0_1_350px] min-w-[280px] bg-card border border-line rounded-xl2 p-5 shadow-[0_20px_44px_-26px_rgba(40,30,10,0.4)]">
            <div className="flex gap-3.5 items-start">
              <ImageSlot id="foot-cart" shape="rounded" radius={12} className="w-[66px] h-[78px] shrink-0 border border-line" />
              <div className="flex-1">
                <span className="font-serif font-semibold text-[18px] leading-tight">Women's Wellness Gummies</span>
                <div className="flex justify-between items-center mt-1">
                  <span className="text-[12.5px] text-muted">Blueberry Flavour</span>
                  <span className="font-serif text-[16px]">₹999</span>
                </div>
                <div className="flex justify-between items-center mt-3">
                  <div className="flex items-center border border-line rounded-full">
                    <span className="px-3 py-1 text-[15px] cursor-pointer">−</span>
                    <span className="text-[13px] min-w-[18px] text-center">1</span>
                    <span className="px-3 py-1 text-[15px] cursor-pointer">+</span>
                  </div>
                  <span className="font-serif text-[16px]">₹999</span>
                </div>
              </div>
            </div>
            <div className="h-px bg-line my-4" />
            <div className="flex justify-between items-center">
              <span className="text-[15px]">Subtotal</span>
              <span className="font-serif text-[24px]">₹999</span>
            </div>
          </div>
        </div>

        <div className="h-px bg-line my-7 lg:my-9" />

        <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3.5">
          <Badge icon={<TruckIcon size={18} stroke="#B49A63" />} label="FSSAI Approved" />
          <span className="w-px h-[18px] bg-line" />
          <Badge icon={<ShieldIcon size={18} stroke="#B49A63" />} label="GMP Certified Manufacturing" />
          <span className="w-px h-[18px] bg-line" />
          <Badge icon={<SupportIcon size={18} stroke="#B49A63" />} label="Customer Support" />
        </div>
        <div className="text-center mt-6 text-[11.5px] text-muted tracking-wide">
          © 2026 Wellvia · Crafted with care for everyday wellness
        </div>
      </div>
    </footer>
  );
}

function Badge({ icon, label }) {
  return (
    <div className="flex items-center gap-2.5 text-[12.5px] text-muted">
      {icon}
      {label}
    </div>
  );
}
