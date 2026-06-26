import Footer from '../components/Footer';
import { MailIcon } from '../components/Icons';

const inputCls = 'w-full bg-bg border border-line rounded-xl px-[17px] py-[15px] text-[14px]';

const INFO = [
  { icon: '✉', label: 'Email', value: 'care@wellvia.in' },
  { icon: '☎', label: 'Phone', value: '+91 1800 123 456' },
  { icon: '◈', label: 'Hours', value: 'Mon–Sat, 9am–7pm IST' },
];

export default function ContactPage() {
  return (
    <>
      <section className="grid md:grid-cols-2 gap-6 lg:gap-[52px] px-5 sm:px-10 lg:px-16 pt-9 lg:pt-16 pb-9 items-start">
        <div>
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Contact</div>
          <h1 className="font-serif font-medium text-[clamp(34px,4.4vw,54px)] m-0 mb-4 leading-[1.04]">We'd love to hear from you</h1>
          <p className="text-[15px] text-muted leading-[1.7] m-0 mb-7 font-light">Questions about a product or your order? Our care team replies within one business day.</p>
          <div className="flex flex-col gap-[18px]">
            {INFO.map((c) => (
              <div key={c.label} className="flex items-center gap-3.5">
                <span className="w-[42px] h-[42px] rounded-full border border-line flex items-center justify-center shrink-0 text-green text-[18px]">{c.icon}</span>
                <div>
                  <div className="text-[11px] tracking-[0.1em] uppercase text-muted">{c.label}</div>
                  <div className="text-[14.5px]">{c.value}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="bg-card border border-line rounded-xl3 p-6 lg:p-9">
          <div className="flex flex-col gap-3.5">
            <input placeholder="Your Name" className={inputCls} />
            <input placeholder="Email Address" className={inputCls} />
            <select className={`${inputCls} cursor-pointer`}>
              <option>General enquiry</option><option>Order support</option><option>Wholesale</option><option>Press</option>
            </select>
            <textarea placeholder="How can we help?" rows={4} className={`${inputCls} resize-y`} />
            <button className="bg-green text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-greenh flex items-center justify-center gap-2">
              <MailIcon size={16} stroke="#fff" />Send Message
            </button>
          </div>
        </div>
      </section>

      <Footer />
    </>
  );
}
