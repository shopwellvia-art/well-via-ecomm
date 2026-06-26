import AccountLayout from '../components/AccountLayout';
import ImageSlot from '../components/ImageSlot';
import { useCart } from '../context/CartContext';
import { PRODUCTS, formatINR } from '../data/products';
import { HeartIcon } from '../components/Icons';

const WISHLIST_IDS = [5, 3, 7];

export default function WishlistPage() {
  const { addAndOpen } = useCart();
  const items = PRODUCTS.filter((p) => WISHLIST_IDS.includes(p.id));

  return (
    <AccountLayout>
      <h1 className="font-serif font-medium text-[clamp(28px,3.4vw,40px)] m-0 mb-1.5">Wishlist</h1>
      <p className="text-[14px] text-muted m-0 mb-6 font-light">Rituals you're saving for later.</p>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {items.map((p) => (
          <article key={p.id} className="bg-card border border-line rounded-xl2 overflow-hidden flex flex-col">
            <div className="relative" style={{ background: 'linear-gradient(160deg,#efe9df,#e4dccd)' }}>
              <ImageSlot id={`prod-${p.id}`} placeholder="Drop photo" className="w-full h-[170px]" />
              <span className="absolute top-2.5 right-2.5 w-[30px] h-[30px] rounded-full bg-card flex items-center justify-center cursor-pointer">
                <HeartIcon size={15} filled stroke="#183A2E" />
              </span>
            </div>
            <div className="p-4 flex flex-col flex-1">
              <div className="font-serif text-[18px] mb-[3px]">{p.name}</div>
              <div className="text-[12px] text-muted mb-3 flex-1">{p.flavor}</div>
              <div className="flex items-center justify-between">
                <span className="font-serif text-[18px]">{formatINR(p.price)}</span>
                <button onClick={() => addAndOpen(p.id)} className="bg-green text-white border-0 rounded-full px-[18px] py-[9px] text-[12px] cursor-pointer hover:bg-greenh">Add</button>
              </div>
            </div>
          </article>
        ))}
      </div>
    </AccountLayout>
  );
}
