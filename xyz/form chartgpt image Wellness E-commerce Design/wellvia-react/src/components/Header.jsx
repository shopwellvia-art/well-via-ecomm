import { Link, NavLink } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import Logo, { LeafMark } from './Logo';
import { SearchIcon, BagIcon, HeartIcon, UserIcon, MenuIcon } from './Icons';

function CartButton() {
  const { count, open } = useCart();
  return (
    <button onClick={open} className="relative cursor-pointer bg-transparent border-0 p-0 text-ink" aria-label="Open cart">
      <BagIcon size={19} strokeWidth={1.4} />
      <span className="absolute -top-2 -right-2 bg-green text-white text-[9px] min-w-[15px] h-[15px] rounded-full flex items-center justify-center px-1">
        {count}
      </span>
    </button>
  );
}

const NAV = [
  { to: '/shop', label: 'Shop All' },
  { to: '/shop', label: 'Rituals' },
  { to: '/about', label: 'Our Story' },
  { to: '/account', label: 'Account' },
];

export default function Header() {
  return (
    <header className="sticky top-0 z-40 bg-bg/90 backdrop-blur-md border-b border-line">
      {/* Desktop / tablet */}
      <div className="hidden md:grid grid-cols-[1fr_auto_1fr] items-center gap-4 py-3.5 px-5 lg:px-[52px]">
        <Logo size="sm" stacked={false} />
        <Logo size="lg" stacked />
        <nav className="flex items-center justify-end gap-4 lg:gap-7 text-[12.5px] tracking-[0.12em] uppercase text-ink">
          {NAV.map((n, i) => (
            <NavLink key={i} to={n.to} className="cursor-pointer hover:text-green no-underline text-ink">
              {n.label}
            </NavLink>
          ))}
          <span className="w-px h-[18px] bg-line" />
          <Link to="/shop" className="text-ink"><SearchIcon size={18} strokeWidth={1.4} /></Link>
          <Link to="/account/wishlist" className="text-ink"><HeartIcon size={18} strokeWidth={1.4} /></Link>
          <Link to="/account" className="text-ink"><UserIcon size={18} strokeWidth={1.4} /></Link>
          <CartButton />
        </nav>
      </div>

      {/* Mobile */}
      <div className="grid md:hidden grid-cols-[1fr_auto_1fr] items-center px-[18px] py-[13px]">
        <button className="bg-transparent border-0 p-0 text-ink justify-self-start" aria-label="Menu">
          <MenuIcon size={22} />
        </button>
        <Link to="/" className="flex items-center gap-1.5 no-underline">
          <LeafMark size={22} dot={false} />
          <span className="font-display text-[17px] tracking-[0.2em] font-medium text-green pl-[0.2em]">WELLVIA</span>
        </Link>
        <div className="flex items-center justify-end gap-4">
          <Link to="/account" className="text-ink"><UserIcon size={18} strokeWidth={1.4} /></Link>
          <CartButton />
        </div>
      </div>
    </header>
  );
}
