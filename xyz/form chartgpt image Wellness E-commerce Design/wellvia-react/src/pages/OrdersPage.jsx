import { useNavigate } from 'react-router-dom';
import AccountLayout from '../components/AccountLayout';
import ImageSlot from '../components/ImageSlot';
import { ORDERS, formatINR } from '../data/products';

const statusColor = (s) =>
  s === 'Delivered' ? 'bg-green' : s === 'Shipped' ? 'bg-gold' : 'bg-[#9b9588]';

export default function OrdersPage() {
  const navigate = useNavigate();
  return (
    <AccountLayout>
      <h1 className="font-serif font-medium text-[clamp(28px,3.4vw,40px)] m-0 mb-1.5">Your Orders</h1>
      <p className="text-[14px] text-muted m-0 mb-6 font-light">Track, review and re-order your rituals.</p>

      <div className="flex flex-col gap-3.5">
        {ORDERS.map((o) => (
          <div key={o.id} className="bg-card border border-line rounded-xl2 p-5 flex flex-wrap gap-4 items-center">
            <ImageSlot id={`ord-${o.id}`} shape="rounded" radius={12} className="w-[62px] h-[72px] shrink-0 border border-line" />
            <div className="flex-1 min-w-[160px]">
              <div className="flex items-center gap-2.5 mb-1">
                <span className="text-[15px]">{o.id}</span>
                <span className={`text-[10.5px] tracking-wide text-white px-[11px] py-[3px] rounded-full ${statusColor(o.status)}`}>{o.status}</span>
              </div>
              <div className="text-[12.5px] text-muted">{o.date} · {o.items} {o.items > 1 ? 'items' : 'item'}</div>
            </div>
            <div className="font-serif text-[20px]">{formatINR(o.total)}</div>
            <button onClick={() => navigate(`/account/orders/${o.id}`)} className="bg-transparent border border-line rounded-full px-[22px] py-[11px] text-[13px] cursor-pointer hover:border-green">View</button>
          </div>
        ))}
      </div>
    </AccountLayout>
  );
}
