import { useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const emptyAddress = {
  fullName: '',
  mobile: '',
  address: '',
  locality: '',
  landmark: '',
  pincode: '',
  city: '',
  state: '',
  label: 'Home',
};

export default function CheckoutAddress({ initialPincode, onBack, onSave }) {
    const navigate = useNavigate();
  const [form, setForm] = useState({ ...emptyAddress, pincode: initialPincode || '' });

  const update = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }));

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave(form);
  };

  return (
    <div className="min-h-screen bg-[#faf9f6]">
      <div className="mx-auto max-w-2xl px-6 py-8">
        <div className="mb-6 flex items-center gap-3">
         <button
            type="button"
            onClick={()=>navigate(-1)}
            aria-label="Go back"
            className="flex h-8 w-8 items-center justify-center rounded-full text-gray-700 hover:bg-gray-100"
          >
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-lg font-semibold text-gray-900">Checkout</h1>
        </div>

        <form onSubmit={handleSubmit} className="rounded-2xl border border-gray-200 bg-white p-6">
          <h2 className="mb-5 text-base font-semibold text-gray-900">Add Delivery Address</h2>

          <div className="mb-4">
            <label className="mb-1 block text-xs font-medium text-gray-500">Full Name *</label>
            <input
              required
              value={form.fullName}
              onChange={update('fullName')}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
            />
          </div>

          <div className="mb-4">
            <label className="mb-1 block text-xs font-medium text-gray-500">Mobile Number *</label>
            <input
              required
              inputMode="numeric"
              maxLength={10}
              value={form.mobile}
              onChange={(e) => setForm((f) => ({ ...f, mobile: e.target.value.replace(/\D/g, '') }))}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
            />
          </div>

          <div className="mb-4">
            <label className="mb-1 block text-xs font-medium text-gray-500">Address *</label>
            <input
              required
              placeholder="House No, Building, Street"
              value={form.address}
              onChange={update('address')}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
            />
          </div>

          <div className="mb-4">
            <label className="mb-1 block text-xs font-medium text-gray-500">Locality *</label>
            <input
              required
              value={form.locality}
              onChange={update('locality')}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
            />
          </div>

          <div className="mb-4">
            <label className="mb-1 block text-xs font-medium text-gray-500">Landmark (optional)</label>
            <input
              value={form.landmark}
              onChange={update('landmark')}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
            />
          </div>

          <div className="mb-4 grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">Pincode *</label>
              <input
                required
                inputMode="numeric"
                maxLength={6}
                value={form.pincode}
                onChange={(e) => setForm((f) => ({ ...f, pincode: e.target.value.replace(/\D/g, '') }))}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-gray-500">City *</label>
              <input
                required
                value={form.city}
                onChange={update('city')}
                className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
              />
            </div>
          </div>

          <div className="mb-5">
            <label className="mb-1 block text-xs font-medium text-gray-500">State *</label>
            <input
              required
              value={form.state}
              onChange={update('state')}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-emerald-700"
            />
          </div>

          <div className="mb-6 flex items-center gap-2">
            {['Home', 'Work'].map((label) => (
              <button
                key={label}
                type="button"
                onClick={() => setForm((f) => ({ ...f, label }))}
                className={`rounded-lg border px-4 py-1.5 text-sm font-medium ${
                  form.label === label
                    ? 'border-[#08112C] bg-[#08112C] text-white'
                    : 'border-gray-200 text-gray-600'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <button
            type="submit"
            onClick={() => navigate("/checkoutfinal")}
            className="w-full rounded-xl bg-[#08112C] py-3.5 font-medium text-white hover:bg-[#08112C]"
          >
            Save Your Address
          </button>
        </form>
      </div>
    </div>
  );
}