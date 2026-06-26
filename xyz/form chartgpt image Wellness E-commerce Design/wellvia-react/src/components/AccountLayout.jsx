import { NavLink, useNavigate } from 'react-router-dom';
import ImageSlot from './ImageSlot';

const NAV = [
  { to: '/account', label: 'Account & Security', end: true },
  { to: '/account/addresses', label: 'Addresses' },
  { to: '/account/orders', label: 'Orders' },
  { to: '/account/wishlist', label: 'Wishlist' },
  { to: '/account/rewards', label: 'Rewards' },
];

/**
 * AccountLayout — sidebar shell shared by every /account/* page.
 */
export default function AccountLayout({ children }) {
  const navigate = useNavigate();
  return (
    <main className="px-5 sm:px-10 lg:px-14 py-6 lg:py-11">
      <div className="max-w-[1120px] mx-auto grid md:grid-cols-[260px_1fr] gap-5 lg:gap-9 items-start">
        <aside className="bg-card border border-line rounded-[20px] p-[22px] md:sticky md:top-[88px]">
          <div className="flex items-center gap-3.5 pb-[18px] border-b border-line mb-4">
            <ImageSlot id="acct-avatar" shape="circle" className="w-12 h-12 shrink-0" />
            <div>
              <div className="font-serif text-[19px] leading-tight">Aditi K.</div>
              <div className="text-[11.5px] text-muted">aditi.k@email.com</div>
            </div>
          </div>
          <div className="flex flex-col gap-[3px]">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end}
                className={({ isActive }) => `px-3.5 py-[11px] rounded-xl text-[13.5px] cursor-pointer transition-colors no-underline ${isActive ? 'bg-green text-white' : 'text-ink hover:bg-line/40'}`}>
                {n.label}
              </NavLink>
            ))}
            <div className="h-px bg-line my-2" />
            <button onClick={() => navigate('/login')} className="text-left px-3.5 py-[11px] rounded-xl text-[13.5px] text-muted cursor-pointer bg-transparent border-0">Sign Out</button>
          </div>
        </aside>

        <section className="min-h-[60vh]">{children}</section>
      </div>
    </main>
  );
}
