import { ArrowLeft, ShoppingCart, Lock, Tag, LogIn, Quote } from 'lucide-react';
import { Link } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { useState, useEffect } from 'react';
import { Stars } from '@/components/storefront/Icons';
import { useNavigate } from "react-router-dom";

const REVIEWS = [
  {
    quote:
      "Finally, a wellness routine I actually stick to. Tastes great and fits effortlessly into my day.",
    name: "Priya S.",
    stars: 5,
  },
  {
    quote:
      "The gummies are delicious and I've actually been consistent for the first time.",
    name: "Ananya R.",
    stars: 5,
  },
  {
    quote:
      "Loved the packaging, the taste, and the results. Highly recommend!",
    name: "Rohan M.",
    stars: 4,
  },
  {
    quote:
      "My sleep quality improved within a couple of weeks. Amazing experience.",
    name: "Sneha K.",
    stars: 5,
  },
  {
    quote:
      "Simple, effective, and something I genuinely look forward to every day.",
    name: "Aarav P.",
    stars: 5,
  },
];
const AVATARS = [
  { id: 0, name: "Priya S.", image: "/female1.png" },
  { id: 1, name: "Rohan M.", image: "/male1.png" },
  { id: 2, name: "Ananya R.", image: "/female2.png" },
  { id: 3, name: "Vatsal G.", image: "/male2.png" },
  { id: 4, name: "Aarav P.", image: "/male3.png" },
];
export default function CheckoutFirst({
  cartItems = [],
  onLoginClick,
}) {
  const totalMRP = cartItems.reduce(
    (sum, item) => sum + item.price * item.qty,
    0
  );

  const shipping = 50;
  const discount = 25;
  const finalTotal = totalMRP - discount;

    const [reviewIndex, setReviewIndex] = useState(0);
    const [showAddressModal, setShowAddressModal] = useState(false);
    const navigate = useNavigate();


    useEffect(() => {
      const interval = setInterval(() => {
        setReviewIndex((prev) => (prev + 1) % REVIEWS.length);
      }, 5000);
    
      return () => clearInterval(interval);
    }, []);
  

  return (
    <div className="min-h-screen bg-[#faf9f6]">
      <div className="mx-auto max-w-6xl px-6 py-8">
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

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-[1fr_380px]">
          {/* Left column */}
<div>
  <div className="mb-6">
    <p className="mb-2 text-sm font-medium text-gray-600">
      Step 1 of 3
    </p>

    <div className="flex gap-1.5">
      <div className="h-1.5 flex-1 rounded-full bg-[#08112C]" />
      <div className="h-1.5 flex-1 rounded-full bg-gray-200" />
      <div className="h-1.5 flex-1 rounded-full bg-gray-200" />
    </div>
  </div>

  {/* Cart Items */}
  <div className="mb-6 space-y-4">
    {cartItems.map((item) => (
      <div
        key={item.id}
        className="flex w-full items-center gap-4 rounded-2xl border border-gray-200 bg-white p-4"
      >
        <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-lg bg-purple-100">
          <div className="h-12 w-9 rounded-sm bg-purple-700" />
        </div>

        <div className="flex-1">
          <p className="font-medium text-gray-900">{item.name}</p>

          <p className="text-sm text-gray-500">
            {item.variant}
            <span className="mx-1">·</span>
            qty: {item.qty}
          </p>
        </div>

        <span className="rounded-md bg-emerald-900 px-3 py-1.5 text-sm font-medium text-white">
          ₹{item.price}
        </span>
      </div>
    ))}
  </div>

  {/* Login */}
  <div className="mb-6 rounded-2xl border border-gray-200 bg-white p-5">
    <div className="mb-4 flex items-center gap-2">
      <LogIn size={18} className="text-gray-500" />

      <h3 className="font-medium text-gray-900">
        Login to Continue
      </h3>
    </div>

    <div className="flex gap-3">
      <input
  type="tel"
  placeholder="Enter Mobile Number"
  className="w-[260px] rounded-lg border border-gray-300 px-4 py-3 focus:outline-none focus:ring-1 focus:ring-emerald-700"
/>

      <button
        type="button"
        onClick={() => setShowAddressModal(true)}
        className="rounded-lg bg-[#08112C] px-6 py-3 text-white hover:bg-[#08112C]"
      >
        Login
      </button>
    </div>
  </div>

  {/* Delivery Address */}
  
</div>
          
          {/* Right sidebar */}
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
                  <p className="mb-2 text-xs text-[#08112C]">
                    You're saving more! Keep going, you are doing great
                  </p>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 flex-1 rounded-full bg-white border border-[#08112C]">
                      <div className="h-1.5 w-[12%] rounded-full bg-[#08112C]" />
                    </div>
                    <span className="whitespace-nowrap text-xs font-medium text-[#08112C]">12% Savings</span>
                  </div>
                </div>
              </div>
            </div>

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
        <section className="px-5 sm:px-10 lg:px-16 py-12 lg:py-[76px]">
          <h2 className="font-wserif font-semibold text-[clamp(28px,3.4vw,44px)] text-wink text-center m-0 mb-10">
            The Reviews Behind the Routine
          </h2>
        
          {/* Changed grid ratio on mobile to 0.95fr_1.05fr for a bigger left column */}
          <div className="max-w-[1080px] mx-auto border border-wgreen/40 rounded-xl3 bg-wcard/40 p-3 sm:p-6 md:p-10 grid grid-cols-[0.95fr_1.05fr] md:grid-cols-[1fr_1.25fr] gap-3 sm:gap-6 md:gap-8 items-center">
            
            <div className="flex items-center justify-center" aria-hidden="true">
              {/* Wrapper to hold the floating avatars positioned exactly like your image */}
              <div className="relative aspect-square w-full max-w-[140px] sm:max-w-[180px] md:max-w-[340px] rounded-xl2">
                {AVATARS.map((avatar, index) => {
                  // Absolute positions mapped to match your mockup layout
                  const positions = [
                    'top-[10%] left-[8%] w-[22%]',    
                    'top-[25%] right-[8%] w-[24%]',  
                    'top-[38%] left-[28%] w-[28%]',  
                    'bottom-[8%] left-[8%] w-[22%]',  
                    'bottom-[8%] right-[12%] w-[22%]',
                  ];
        
                  return (
                    <img
                      key={avatar.id}
                      src={avatar.image}
                      alt={avatar.name}
                      className={`absolute rounded-full object-cover transition-all duration-500 ${positions[index]}`}
                      style={{
                        boxShadow: index === reviewIndex ? "0 0 16px 8px rgba(135, 182, 169, 0.6)" : "none",
                        border: index === reviewIndex ? "3px solid #87B6A9" : "none",
                        zIndex: index === reviewIndex ? 10 : 1,
                      }}
                    />
                  );
                })}
              </div>
            </div>
        
          {/* Quote card */}
        <div className="relative bg-white rounded-xl2 shadow-[0_28px_60px_-34px_rgba(30,30,26,0.45)] px-3 py-3 sm:px-6 sm:pt-10 sm:pb-1 md:px-9 md:pt-14 md:pb-2">
          
          {/* Top Right Quote Image - Static */}
          <img 
            src="/up-quote.png" 
            alt="" 
            aria-hidden="true"
            className="absolute top-2 right-3 sm:top-5 sm:right-7 w-6 sm:w-8 md:w-10 h-auto pointer-events-none select-none"
          />
        
          {/* Only review text & stars animate */}
          <AnimatePresence mode="wait">
            <motion.div
              key={reviewIndex}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.7 }}
            >
              <Stars count={REVIEWS[reviewIndex].stars} className="text-[11px] sm:text-[18px] md:text-[22px]" />
        
              <blockquote className="font-wserif font-medium text-[11px] sm:text-[17px] md:text-[clamp(18px,1.8vw,23px)] leading-[1.25] sm:leading-[1.3] md:leading-[1.5] text-wink m-0 my-0.5 sm:mt-4 sm:mb-5">
                “{REVIEWS[reviewIndex].quote}”
              </blockquote>
        
              <div className="font-wserif text-[10px] sm:text-[15px] md:text-[17px] text-wink/80">
                {REVIEWS[reviewIndex].name}
              </div>
            </motion.div>
          </AnimatePresence>
        
          {/* Bottom Left Quote Image - Static (Moved outside motion.div) */}
          <div className="flex justify-start mt-1 sm:mt-4">
            <img 
              src="/down-quote.png" 
              alt="" 
              aria-hidden="true"
              className="w-7 sm:w-9 md:w-12 h-auto opacity-40 pointer-events-none select-none"
            />
          </div>
        </div>
        
          </div>
        
          <div className="text-center mt-4">
            <Link
              to="/products"
              className="font-wserif text-[19px] text-wink underline underline-offset-[6px] hover:text-wgreen transition-colors"
            >
              Check All Reviews →
            </Link>
          </div>
        </section>
                  {showAddressModal && (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
    <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl">

      <h2 className="mb-5 text-xl font-semibold text-gray-900">
        Delivery Address
      </h2>

      <input
        type="text"
        placeholder="Enter Pincode"
        className="mb-4 w-full rounded-lg border border-gray-300 px-4 py-3 focus:outline-none focus:ring-2 focus:ring-emerald-700"
      />

      <div className="flex justify-end gap-3">

        <button
          onClick={() => setShowAddressModal(false)}
          className="rounded-lg border border-gray-300 px-5 py-3"
        >
          Cancel
        </button>

        <button
        onClick={() => navigate("/address")}
          className="rounded-lg bg-[#08112C] px-6 py-3 text-white hover:bg-[#08112C]"
        >
          Continue
        </button>

      </div>
    </div>
  </div>
)}
      </div>
    </div>
  );
}