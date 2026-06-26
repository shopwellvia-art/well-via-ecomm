import { Link } from 'react-router-dom';
import Footer from '../components/Footer';

const PRESS = [
  { outlet: 'Vogue India', date: 'Jun 2026', title: 'The wellness brand redefining the daily ritual' },
  { outlet: 'Forbes', date: 'May 2026', title: 'How Wellvia built a clean-label cult following' },
  { outlet: 'Elle', date: 'Apr 2026', title: '12 supplements worth the shelf space' },
  { outlet: 'Mint Lounge', date: 'Mar 2026', title: 'Inside the studio of a modern wellness house' },
];

export default function PressPage() {
  return (
    <>
      <section className="text-center px-5 sm:px-10 lg:px-16 pt-10 lg:pt-[72px] pb-6 lg:pb-9">
        <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Press</div>
        <h1 className="font-serif font-medium text-[clamp(36px,4.6vw,58px)] m-0 mb-3">In the press</h1>
        <p className="text-[15px] text-muted max-w-[520px] mx-auto font-light">Stories, features and resources for media. For enquiries, <Link to="/contact" className="text-green no-underline">contact our team</Link>.</p>
      </section>

      <section className="px-5 sm:px-10 lg:px-16 pb-10 lg:pb-16">
        <div className="max-w-[920px] mx-auto flex flex-col gap-3.5">
          {PRESS.map((p, i) => (
            <div key={i} className="bg-card border border-line rounded-2xl px-6 py-5 flex flex-wrap gap-3 items-center justify-between">
              <div className="flex-1 min-w-[200px]">
                <div className="text-[11.5px] tracking-[0.12em] uppercase text-gold mb-1.5">{p.outlet} · {p.date}</div>
                <div className="font-serif text-[21px] leading-tight">{p.title}</div>
              </div>
              <span className="text-[13px] text-green cursor-pointer whitespace-nowrap">Read →</span>
            </div>
          ))}
        </div>
      </section>

      <Footer />
    </>
  );
}
