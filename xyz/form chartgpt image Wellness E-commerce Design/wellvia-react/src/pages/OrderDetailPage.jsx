import { Link, useParams } from 'react-router-dom';
import AccountLayout from '../components/AccountLayout';
import ImageSlot from '../components/ImageSlot';

const STEPS = ['Placed', 'Packed', 'Shipped', 'Delivered'];

export default function OrderDetailPage() {
  const { id = 'WV-10428' } = useParams();
  return (
    <AccountLayout>
      <Link to="/account/orders" className="text-[13px] text-green no-underline mb-3.5 inline-block">← Back to orders</Link>
      <div className="flex flex-wrap justify-between items-end gap-3 mb-[22px]">
        <div>
          <h1 className="font-serif font-medium text-[clamp(26px,3.2vw,38px)] m-0 mb-1">Order {id}</h1>
          <p className="text-[13.5px] text-muted m-0 font-light">Placed 12 Jun 2026</p>
        </div>
        <span className="text-[11px] bg-green text-white px-4 py-1.5 rounded-full tracking-wide">Delivered</span>
      </div>

      {/* Timeline */}
      <div className="bg-card border border-line rounded-xl2 p-6 mb-[18px]">
        <div className="flex justify-between relative">
          <div className="absolute top-[6px] left-[8%] right-[8%] h-0.5 bg-gold" />
          {STEPS.map((s) => (
            <div key={s} className="flex flex-col items-center gap-2 relative z-[1] flex-1">
              <span className="w-3.5 h-3.5 rounded-full bg-gold border-2 border-card" />
              <span className="text-[11.5px] text-ink text-center">{s}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        <div className="bg-card border border-line rounded-xl2 p-[22px]">
          <div className="font-serif text-[18px] mb-3.5">Items</div>
          <div className="flex gap-3 items-center pb-3.5 border-b border-line mb-3.5">
            <ImageSlot id="od-item" shape="rounded" radius={10} className="w-[54px] h-[62px] shrink-0 border border-line" />
            <div className="flex-1"><div className="text-[14px]">Women's Wellness Gummies</div><div className="text-[11.5px] text-muted">Blueberry · Qty 2</div></div>
            <div className="font-serif text-[16px]">₹1,998</div>
          </div>
          <div className="flex justify-between text-[13.5px] text-muted"><span>Total Paid</span><span className="font-serif text-[18px] text-ink">₹1,998</span></div>
        </div>
        <div className="bg-card border border-line rounded-xl2 p-[22px]">
          <div className="font-serif text-[18px] mb-3.5">Delivery</div>
          <div className="text-[13.5px] text-muted leading-[1.7] font-light">Aditi Kapoor<br />14 Rose Lane, Bandra West<br />Mumbai 400050, India</div>
          <div className="flex gap-3 mt-[18px]">
            <button className="flex-1 bg-green text-white border-0 rounded-full py-2.5 text-[13px] cursor-pointer hover:bg-greenh">Re-order</button>
            <button className="flex-1 bg-transparent border border-line rounded-full py-2.5 text-[13px] cursor-pointer">Invoice</button>
          </div>
        </div>
      </div>
    </AccountLayout>
  );
}
