import Footer from '../components/Footer';

const ROLES = [
  { title: 'Senior Product Designer', meta: 'Design · Mumbai · Full-time' },
  { title: 'Nutrition Scientist', meta: 'R&D · Bangalore · Full-time' },
  { title: 'Growth Marketing Lead', meta: 'Marketing · Remote · Full-time' },
  { title: 'Customer Care Associate', meta: 'Operations · Mumbai · Full-time' },
];

export default function CareersPage() {
  return (
    <>
      <section className="text-center px-5 sm:px-10 lg:px-16 pt-10 lg:pt-[72px] pb-7 lg:pb-10">
        <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Careers</div>
        <h1 className="font-serif font-medium text-[clamp(36px,4.6vw,58px)] m-0 mb-3.5 leading-[1.04]">Build wellness worth trusting</h1>
        <p className="text-[15.5px] text-muted max-w-[560px] mx-auto font-light leading-[1.7]">Join a team obsessed with clean nutrition, beautiful design, and real outcomes.</p>
      </section>

      <section className="px-5 sm:px-10 lg:px-16 pb-10 lg:pb-16">
        <div className="max-w-[860px] mx-auto flex flex-col gap-3.5">
          {ROLES.map((r) => (
            <div key={r.title} className="bg-card border border-line rounded-2xl px-6 py-5 flex flex-wrap gap-3.5 items-center justify-between">
              <div>
                <div className="font-serif text-[20px] mb-1">{r.title}</div>
                <div className="text-[12.5px] text-muted">{r.meta}</div>
              </div>
              <button className="bg-transparent border border-green text-green rounded-full px-6 py-[11px] text-[13px] cursor-pointer hover:bg-green hover:text-white">Apply</button>
            </div>
          ))}
        </div>
      </section>

      <Footer />
    </>
  );
}
