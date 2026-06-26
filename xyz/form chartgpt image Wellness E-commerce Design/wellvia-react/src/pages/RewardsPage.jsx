import AccountLayout from '../components/AccountLayout';

const PERKS = [
  { cost: '250 pts', title: '₹150 off', desc: 'On any order above ₹999.' },
  { cost: '500 pts', title: 'Free Gummies', desc: 'A full jar, on the house.' },
  { cost: '1000 pts', title: 'Ritual Bundle', desc: 'Curated 3-product set.' },
];

export default function RewardsPage() {
  return (
    <AccountLayout>
      <h1 className="font-serif font-medium text-[clamp(28px,3.4vw,40px)] m-0 mb-1.5">Wellvia Rewards</h1>
      <p className="text-[14px] text-muted m-0 mb-[22px] font-light">Earn with every ritual. Redeem on what you love.</p>

      <div className="bg-green text-[#f3efe6] rounded-xl3 p-6 lg:p-[34px] mb-[18px] flex flex-wrap justify-between items-center gap-[18px]">
        <div>
          <div className="text-[12px] tracking-[0.16em] uppercase text-[#f3efe6]/70 mb-2">Glow Member</div>
          <div className="font-serif text-[clamp(40px,5vw,56px)] leading-none">1,240 <span className="text-[20px]">pts</span></div>
          <div className="text-[12.5px] text-[#f3efe6]/70 mt-2">260 points to Radiance tier</div>
        </div>
        <div className="w-[120px] h-[120px] rounded-full border-2 border-gold/60 flex items-center justify-center text-center font-serif text-[15px] text-gold">Glow<br />Tier</div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3.5">
        {PERKS.map((p) => (
          <div key={p.title} className="bg-card border border-line rounded-2xl p-5">
            <div className="font-serif text-[22px] text-gold mb-2">{p.cost}</div>
            <div className="text-[14px] mb-1">{p.title}</div>
            <div className="text-[12px] text-muted font-light">{p.desc}</div>
          </div>
        ))}
      </div>
    </AccountLayout>
  );
}
