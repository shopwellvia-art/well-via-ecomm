import AccountLayout from '../components/AccountLayout';
import { ADDRESSES } from '../data/products';

export default function AddressesPage() {
  return (
    <AccountLayout>
      <div className="flex flex-wrap justify-between items-end gap-3 mb-6">
        <div>
          <h1 className="font-serif font-medium text-[clamp(28px,3.4vw,40px)] m-0 mb-1">Addresses</h1>
          <p className="text-[14px] text-muted m-0 font-light">Saved delivery locations.</p>
        </div>
        <button className="bg-green text-white border-0 rounded-full px-6 py-3 text-[13.5px] cursor-pointer hover:bg-greenh">+ Add Address</button>
      </div>

      <div className="grid sm:grid-cols-2 gap-4">
        {ADDRESSES.map((a, i) => (
          <div key={i} className={`bg-card border border-line rounded-xl2 p-[22px] ${a.isDefault ? 'shadow-[0_0_0_1.5px_#183A2E]' : ''}`}>
            <div className="flex justify-between items-center mb-3">
              <span className="text-[11px] tracking-[0.12em] uppercase text-gold">{a.label}</span>
              {a.isDefault && <span className="text-[10.5px] bg-green text-white px-2.5 py-[3px] rounded-full">Default</span>}
            </div>
            <div className="text-[15px] mb-1">{a.name}</div>
            <div className="text-[13px] text-muted leading-[1.6] font-light">{a.line}<br />{a.cityline}<br />{a.phone}</div>
            <div className="flex gap-[18px] mt-4 text-[12.5px]">
              <span className="text-green cursor-pointer">Edit</span>
              <span className="text-muted cursor-pointer">Remove</span>
            </div>
          </div>
        ))}
      </div>
    </AccountLayout>
  );
}
