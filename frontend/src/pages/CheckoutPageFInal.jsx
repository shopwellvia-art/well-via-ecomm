import { useState } from 'react';
import { ArrowLeft, MapPin, ShoppingCart, Lock, Tag } from 'lucide-react';


// --- Mock data — wire these up to your real cart / address / payment state ---
const cartItems = [
  {
    id: 1,
    name: 'Sleep Gummies',
    variant: 'Pack of 30',
    qty: 1,
    price: 349,
    image: '/sleep-gummies.png',
  },
  {
    id: 2,
    name: 'Omega Gummies',
    variant: 'Pack of 30',
    qty: 1,
    price: 349,
    image: '/omega-gummies.png',
  },
];

const paymentOptions = [
  { id: 'upi', label: 'Pay by any UPI app', type: 'group' },
  { id: 'googlepay', label: 'Google Pay', group: 'upi' },
  { id: 'paytm', label: 'Paytm', group: 'upi' },
  { id: 'phonepe', label: 'PhonePe', group: 'upi' },
  { id: 'card', label: 'Credit/Debit Cards' },
  { id: 'cod', label: 'Cash On Delivery' },
];

export default function CheckoutPageFinal() {
  const [selectedProduct, setSelectedProduct] = useState(cartItems[0].id);
  const [selectedPayment, setSelectedPayment] = useState('paytm');
  const [couponCode, setCouponCode] = useState('');

  const totalMRP = cartItems.reduce((sum, item) => sum + item.price * item.qty, 0);
  const shipping = 50;
  const discount = 25;
  const totalAmount = totalMRP - shipping - discount + shipping; // shipping is free
  const finalTotal = totalMRP - discount;

  return (
    <div className="min-h-screen bg-[#faf9f6]">
      <div className="mx-auto max-w-6xl px-6 py-8">
        {/* Header */}
        <div className="mb-6 flex items-center gap-3">
          <button
            type="button"
            aria-label="Go back"
            className="flex h-8 w-8 items-center justify-center rounded-full text-gray-700 hover:bg-gray-100"
          >
            <ArrowLeft size={20} />
          </button>
          <h1 className="text-lg font-semibold text-gray-900">Checkout</h1>
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1fr_380px]">
          {/* ---------------- Left column ---------------- */}
          <div>
            {/* Step indicator */}
            <div className="mb-6">
              <p className="mb-2 text-sm font-medium text-gray-600">Step 2 of 3</p>
              <div className="flex gap-1.5">
                <div className="h-1.5 flex-1 rounded-full bg-[#08112C]" />
                <div className="h-1.5 flex-1 rounded-full bg-[#08112C]" />
                <div className="h-1.5 flex-1 rounded-full bg-gray-200" />
              </div>
            </div>

            {/* Address card */}
            <div className="mb-6 rounded-2xl border border-gray-200 bg-white p-5">
              <div className="mb-1 flex items-center gap-2 text-gray-900">
                <MapPin size={18} />
                <span className="font-medium">Address</span>
              </div>
              <p className="text-sm font-medium text-[#08112C]">Delivery by 25 June, Wednesday</p>
            </div>

            {/* Cart items */}
            <div className="mb-8 space-y-4">
              {cartItems.map((item) => {
                const isSelected = selectedProduct === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => setSelectedProduct(item.id)}
                    className={`flex w-full items-center gap-4 rounded-2xl border bg-white p-4 text-left transition-colors ${
                      isSelected ? 'border-2 border-sky-400' : 'border-gray-200'
                    }`}
                  >
                    <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg bg-purple-100">
                      <img
    src={item.image}
    alt={item.name}
    className="h-full w-full object-contain"
  />
                    </div>
                    <div className="flex-1">
                      <p className="font-medium text-gray-900">{item.name}</p>
                      <p className="text-sm text-gray-500">
                        {item.variant} <span className="mx-1">·</span> qty: {item.qty}
                      </p>
                    </div>
                    <span className="rounded-md bg-[#08112C] px-3 py-1.5 text-sm font-medium text-white">
                      ₹{item.price}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Payment methods */}
            <div>
              <h2 className="mb-4 text-lg font-semibold text-gray-900">Payment Methods</h2>

              <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
                <label className="flex cursor-pointer items-center justify-between border-b border-gray-100 px-4 py-3.5">
                  <span className="flex items-center gap-2 text-sm font-medium text-gray-800">
                    <span className="text-xs font-bold tracking-wide text-gray-500">UPI</span>
                    Pay by any UPI app
                  </span>
                  <input
                    type="radio"
                    name="payment"
                    checked={selectedPayment.startsWith('upi') || ['googlepay', 'paytm', 'phonepe'].includes(selectedPayment)}
                    onChange={() => setSelectedPayment('paytm')}
                    className="h-4 w-4 accent-emerald-800"
                  />
                </label>

                <div className="space-y-3 px-4 py-4">
                  {[
                    { id: 'googlepay', label: 'Google Pay' },
                    { id: 'paytm', label: 'Paytm' },
                    { id: 'phonepe', label: 'PhonePe' },
                  ].map((opt) => (
                    <label
                      key={opt.id}
                      className={`flex cursor-pointer items-center justify-between rounded-xl border px-4 py-3 ${
                        selectedPayment === opt.id ? 'border-gray-900' : 'border-gray-200'
                      }`}
                    >
                      <span className="text-sm font-medium text-gray-800">{opt.label}</span>
                      <input
                        type="radio"
                        name="payment"
                        checked={selectedPayment === opt.id}
                        onChange={() => setSelectedPayment(opt.id)}
                        className="h-4 w-4 accent-gray-900"
                      />
                    </label>
                  ))}
                </div>

                <label className="flex cursor-pointer items-center justify-between border-t border-gray-100 px-4 py-3.5">
                  <span className="text-sm font-medium text-gray-800">Credit/Debit Cards</span>
                  <input
                    type="radio"
                    name="payment"
                    checked={selectedPayment === 'card'}
                    onChange={() => setSelectedPayment('card')}
                    className="h-4 w-4 accent-emerald-800"
                  />
                </label>

                <label className="flex cursor-pointer items-center justify-between border-t border-gray-100 px-4 py-3.5">
                  <span className="text-sm font-medium text-gray-800">Cash On Delivery</span>
                  <input
                    type="radio"
                    name="payment"
                    checked={selectedPayment === 'cod'}
                    onChange={() => setSelectedPayment('cod')}
                    className="h-4 w-4 accent-emerald-800"
                  />
                </label>
              </div>
            </div>
          </div>

          {/* ---------------- Right column (sidebar) ---------------- */}
          <div>
            <div className="mb-3 flex items-center justify-end gap-1.5 text-sm font-medium text-gray-700">
              100% secured payment <Lock size={14} />
            </div>

            <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
              <div className="bg-gray-900 py-1.5 text-center text-xs font-medium text-white">
                Prepaid Orders are delivered faster!
              </div>

              <div className="p-5">
                <div className="mb-3 flex items-start justify-between">
                  <div>
                    <div className="mb-1 flex items-center gap-2 font-medium text-gray-900">
                      <ShoppingCart size={16} />
                      Order Overview
                    </div>
                    <p className="text-xs text-gray-500">{cartItems.length} items in your box</p>
                    <span className="mt-2 inline-block rounded-md bg-[#08112C] px-2 py-1 text-xs font-medium text-white">
                      Great Pick — ₹447 Saved
                    </span>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-gray-500">Total Amount</p>
                    <p className="font-semibold text-gray-900">₹{finalTotal}</p>
                    <p className="text-xs text-gray-400 line-through">₹{totalMRP}</p>
                  </div>
                </div>

                <div className="rounded-xl bg-white p-3">
                  <div className="mb-2 flex items-center justify-between text-xs">
                    <span className="text-[#08112C]">You're saving more! Keep going, you are doing great</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 flex-1 rounded-full bg-white border border-[#08112C]">
                      <div className="h-1.5 w-[12%] rounded-full bg-[#08112C]" />
                    </div>
                    <span className="whitespace-nowrap text-xs font-medium text-[#08112C]">12% Savings</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-4 rounded-2xl border border-gray-200 bg-white p-5">
              <h3 className="mb-3 font-medium text-gray-900">Order Summary</h3>
              <div className="space-y-2.5 text-sm">
                <div className="flex items-center justify-between">
                  <span className="text-gray-600">Total MRP :</span>
                  <span className="text-gray-900">₹{totalMRP.toFixed(2)}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-gray-600">Shipping charges :</span>
                  <span className="text-right">
                    <span className="font-medium text-emerald-700">Free </span>
                    <span className="text-gray-400 line-through">₹{shipping}</span>
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-gray-600">Payment Discount :</span>
                  <span className="text-right">
                    <span className="text-gray-900">- ₹{discount}</span>
                    <br />
                    <span className="text-xs text-amber-600">Saved ₹200</span>
                  </span>
                </div>
                <div className="flex items-center justify-between border-t border-gray-100 pt-2.5 font-medium">
                  <span className="text-gray-900">Total Amount :</span>
                  <span className="text-gray-900">₹{finalTotal.toFixed(2)}</span>
                </div>
              </div>
            </div>

            <button
              type="button"
              className="mt-4 w-full rounded-xl bg-[#08112C] py-3.5 font-medium text-white hover:bg-[#08112C]"
            >
              Place Order
            </button>

            <div className="mt-4 rounded-2xl border border-gray-200 bg-white p-4">
              <div className="mb-2 flex items-center justify-between">
                <span className="flex items-center gap-2 text-sm font-medium text-gray-900">
                  <Tag size={16} className="text-emerald-700" />
                  Coupons &amp; Offers
                </span>
                <a href="#offers" className="text-sm font-medium text-emerald-700">
                  Offers
                </a>
              </div>
              <p className="mb-3 text-xs text-gray-500">Save more with coupons and offers</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={couponCode}
                  onChange={(e) => setCouponCode(e.target.value)}
                  placeholder="Enter the coupon code"
                  className="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm placeholder:text-gray-400 focus:outline-none focus:ring-1 focus:ring-emerald-700"
                />
                <button
                  type="button"
                  className="rounded-lg bg-gray-400 px-4 py-2 text-sm font-medium text-white hover:bg-gray-500"
                >
                  Apply Code
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}