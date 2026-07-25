import { Page } from "@/components/layout/Page";
import { useState } from "react";
import { MapPin, Truck, RefreshCcw } from "lucide-react";

const thumbs = [
  "/mini-1.png",
  "/mini-2.png",
  "/mini-3.png"
];

// Simple placeholder box — swap the <ImgPlaceholder> for a real <img src="..." />
// whenever you drop in your actual images.
function ImgPlaceholder({ label, className = "" }) {
  return (
    <div
      className={`flex items-center justify-center border border-dashed border-stone-300 bg-stone-50 text-stone-400 text-xs text-center px-2 ${className}`}
    >
      {label}
    </div>
  );
}

function Accordion({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-stone-200 rounded-lg">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-stone-800"
      >
        {title}
        <span className={`transition-transform ${open ? "rotate-180" : ""}`}>⌄</span>
      </button>
      {open && (
        <div className="px-4 pb-4 text-sm text-stone-600 leading-relaxed">{children}</div>
      )}
    </div>
  );
}

export default function ProductDemoPage() {
  const [qty, setQty] = useState(1);
  const [showReviewModal, setShowReviewModal] = useState(false);

  return (
    <Page>
      <div className="max-w-[1320px] mx-auto px-5 sm:px-10 lg:px-16 py-10">

        {/* ---------- Mobile-only header: title, badge, description, rating ----------
            On desktop (lg+) this is hidden — the same info still renders inside the
            buy box further down, wrapped in "hidden lg:block". This is the only
            duplication used, and it's purely so mobile can show title/description
            BEFORE the product image while desktop keeps its original two-column order. */}
        <div className="lg:hidden mb-5">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-cormorant">Wellvia Sleep Gummies</h1>
            <span className="text-xs bg-[#C99A3C]/15 text-[#8a6420] px-2 py-1 rounded-full whitespace-nowrap">
              Black current
            </span>
          </div>
          <p className="mt-2 text-stone-600 text-sm">
            Healthy sleep gummies to calm your mind and fall asleep faster.
          </p>
          <div className="flex items-center gap-1 mt-3 text-sm">
            <span className="text-[#C99A3C]">★</span>
            <span className="text-stone-500">4.7/5 </span>
          </div>
        </div>

        {/* ---------- Hero: gallery + buy box ---------- */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 lg:gap-12">
          {/* Gallery */}
          <div className="lg:col-span-5">
            <div className="relative rounded-2xl bg-[#FBF7EF] overflow-hidden">
              <span className="absolute top-4 left-4 text-xs tracking-wide text-stone-500">
                Pack of 60
              </span>

              <img
                src="/sleep-gummies.png"
                className="w-full object-contain"
                alt=""
              />
            </div>

            <div className="grid grid-cols-3 gap-3 mt-4">
              {thumbs.map((img, i) => (
                <img
                  key={i}
                  src={img}
                  alt={`Thumbnail ${i + 1}`}
                  className="aspect-square w-full rounded-lg object-cover"
                />
              ))}
            </div>
          </div>

          {/* Buy box */}
          <div className="lg:col-span-7">
            {/* Desktop-only title/badge/description/rating (mirrors the mobile block above) */}
            <div className="hidden lg:block">
              <div className="flex items-center gap-2">
                <h1 className="text-4xl font-cormorant">Wellvia Sleep Gummies</h1>
                <span className="text-xs bg-[#C99A3C]/15 text-[#8a6420] px-2 py-1 rounded-full whitespace-nowrap">
                  Black current
                </span>
              </div>
              <p className="mt-2 text-stone-600">
                Healthy sleep gummies to calm your mind and fall asleep faster.
              </p>

              <div className="flex items-center gap-1 mt-3 text-sm">
                <span className="text-[#C99A3C]">★</span>
                <span className="text-stone-500">4.7/5 </span>
              </div>
            </div>

            <div className="flex items-baseline gap-3 mt-2 lg:mt-5">
              <span className="text-2xl font-medium">Rs 1300</span>
              <span className="text-stone-400 line-through text-sm">Rs 1299</span>
              <span className="border border-stone-300 rounded-full px-3 py-1 text-xs">
                Buy 1 Get 1 Free
              </span>
            </div>

            <div className="mt-4 flex items-center gap-2">
              <div className="flex items-center border border-stone-300 rounded-full">
                <button
                  onClick={() => setQty(Math.max(1, qty - 1))}
                  className="w-9 h-9 text-stone-600"
                >
                  −
                </button>
                <span className="w-8 text-center">{qty}</span>
                <button onClick={() => setQty(qty + 1)} className="w-9 h-9 text-stone-600">
                  +
                </button>
              </div>
              <span className="text-xs text-stone-500 uppercase tracking-wide">Qty</span>
            </div>

            <div
              className="mt-5 w-full lg:w-[70%] rounded-xl px-4 py-3 flex items-center gap-4 bg-cover bg-center"
              style={{ backgroundImage: "url('/purple-bg.png')" }}
            >
              <div className="text-sm">
                <span className="font-medium text-black">Get it for Rs 699</span>
                <div className="text-black text-xs mt-0.5">
                  Use code: <span className="font-semibold">WELLVIA</span>
                </div>
              </div>

              <button className="bg-white text-black text-xs px-3 py-1.5 rounded-full ml-auto lg:ml-40">
                Copy
              </button>
            </div>

            <button className="w-full lg:w-[70%] mt-5 bg-[#391C43] text-white rounded-full py-3 text-sm font-medium">
              Add to cart
            </button>

            <div className="mt-6 w-full lg:w-[70%]">
              <div className="flex items-center gap-2 mb-3">
                <MapPin className="w-4 h-4 text-[#391C43]" />
                <span className="text-sm font-medium text-stone-800">
                  Delivery Availability
                </span>
              </div>

              <div className="flex items-center gap-3">
                <input
                  placeholder="Enter pincode"
                  className="flex-1 border border-stone-300 rounded-full px-4 py-2 text-sm min-w-0"
                />

                <button className="bg-[#08112C] text-white text-sm px-5 py-2 rounded-full whitespace-nowrap">
                  Check
                </button>
              </div>
            </div>

            <div className="mt-4 w-full lg:w-[70%] space-y-3 text-sm text-stone-600">
              <div className="flex items-center gap-2">
                <Truck className="w-4 h-4 text-[#391C43]" />
                <p>Free shipping on orders above Rs 500</p>
              </div>

              <div className="flex items-center gap-2">
                <RefreshCcw className="w-4 h-4 text-[#391C43]" />
                <p className="underline underline-offset-2 cursor-pointer">
                  Refund and replacement policy
                </p>
              </div>
            </div>

            <div className="mt-6 w-full lg:w-[70%] space-y-3">
              <Accordion title="Description" defaultOpen>
                Our Sleep Gummies deliver the benefits of ACV without the sharp
                taste or liquid mess two gummies a day support digestion, metabolism and
                gentle daily detox.
              </Accordion>

              <Accordion title="How to use">
                Take 2 gummies daily, ideally after a meal. Chew thoroughly. Do not exceed the
                recommended daily dose.
              </Accordion>

              <Accordion title="FAQ">
                Common questions about acidity, pregnancy, and interactions with medication
                go here.
              </Accordion>
            </div>

            <p className="w-full lg:w-[70%] text-center text-xs text-stone-500 mt-6">
              🔒 100% Secure Payments
            </p>
          </div>
        </div>
{/* ---------- Why you'll love it ---------- */}
<section className="mt-10 lg:mt-24 border border-stone-200 rounded-2xl px-3 sm:px-8 py-5 sm:py-10">
  <h2 className="text-[22px] lg:text-3xl font-cormorant text-center">
    Why You'll Love It
  </h2>

  <div className="grid grid-cols-4 gap-2 sm:gap-4 lg:gap-8 mt-6 sm:mt-10 text-center">
    {[
      {
        icon: "/emoji.png",
        title: "Fall Asleep Faster",
        copy: "Helps you relax and drift off naturally.",
      },
      {
        icon: "/clock.png",
        title: "Stay Asleep Longer",
        copy: "Supports deep, uninterrupted sleep.",
      },
      {
        icon: "/yoga.png",
        title: "Feel Calm & Relaxed",
        copy: "Reduces stress and quiets your mind.",
      },
      {
        icon: "/bed.png",
        title: "Wake Up Refreshed",
        copy: "No grogginess, just natural energy.",
      },
    ].map((item) => (
      <div key={item.title}>
        <div className="w-10 h-10 sm:w-20 sm:h-20 mx-auto rounded-full bg-[#C99A3C]/15 flex items-center justify-center">
          <img
            src={item.icon}
            alt={item.title}
            className="w-7 h-7 sm:w-10 sm:h-10 object-contain"
          />
        </div>

        <h3 className="mt-2 text-[10px] sm:text-lg font-medium text-stone-800 leading-tight">
          {item.title}
        </h3>

        {/* Hidden on mobile to save space */}
        <p className="hidden sm:block text-sm text-stone-500 mt-2 max-w-[170px] mx-auto leading-relaxed">
          {item.copy}
        </p>
      </div>
    ))}
  </div>
</section>

<img
  src="/product2.png"
  alt=""
  className="mx-auto mt-4 w-full sm:w-[90%] lg:w-[70%] h-auto object-contain"
/>
       {/* ---------- Ingredients ---------- */}
<section className="mt-10 lg:mt-16 border border-stone-200 rounded-2xl px-3 sm:px-8 py-5 sm:py-10">

  <h2 className="text-[22px] lg:text-3xl font-cormorant text-center">
    Clean & Effective Ingredients
  </h2>

  <div className="grid grid-cols-2 lg:grid-cols-2 gap-4 lg:gap-10 items-center mt-6 lg:-mt-10">

    {/* Left */}
    <div>
      <ul className="space-y-4 sm:space-y-6">
        {[
          {
            icon: "/basket.png",
            name: "Melatonin",
            copy: "Regulates sleep cycle and improves quality",
          },
          {
            icon: "/leaf.png",
            name: "L-Theanine",
            copy: "Promotes relaxation without drowsiness",
          },
          {
            icon: "/current.png",
            name: "Vitamin B6",
            copy: "Supports mood balance and recovery",
          },
        ].map((ing) => (
          <li key={ing.name} className="flex gap-2 sm:gap-4 items-center">

            <div className="w-10 h-10 sm:w-14 sm:h-14 rounded-full bg-[#C99A3C]/15 flex items-center justify-center shrink-0">
              <img
                src={ing.icon}
                alt={ing.name}
                className="w-5 h-5 sm:w-8 sm:h-8 object-contain"
              />
            </div>

            <div>
              <p className="text-[11px] sm:text-base font-medium text-stone-800 leading-tight">
                {ing.name}
              </p>

              <p className="text-[9px] sm:text-sm text-stone-500 leading-tight">
                {ing.copy}
              </p>
            </div>

          </li>
        ))}
      </ul>
    </div>

    {/* Right */}
    <div className="flex justify-center">
      <img
        src="/clean1.png"
        alt=""
        className="w-full sm:w-[80%] h-auto object-contain"
      />
    </div>

  </div>
</section>

        {/* ---------- What to expect ---------- */}
<section className="mt-10 lg:mt-16 border border-stone-200 rounded-2xl px-3 sm:px-8 py-5 sm:py-8">

  <h2 className="text-[22px] lg:text-3xl font-cormorant text-center mb-5 lg:mb-0 lg:-mb-10">
    What to Expect
  </h2>

  <div className="grid grid-cols-2 lg:grid-cols-2 gap-4 lg:gap-10 items-center">

    {/* Left */}
    <div>
      <ol className="relative border-l-2 border-[#391C43] ml-2 space-y-4 sm:space-y-8">
        {[
          { title: "30 Mins Before Bed", copy: "Take 2 Sleep Gummies." },
          { title: "Relax & Unwind", copy: "Feel calm, relaxed & stress-free." },
          { title: "Fall Asleep Faster", copy: "Drift off naturally and comfortably." },
          { title: "Wake Refreshed", copy: "Feel rejuvenated and ready for the day." },
        ].map((step) => (
          <li key={step.title} className="ml-4 sm:ml-6">

            <span className="absolute -left-[6px] w-2.5 h-2.5 sm:w-3 sm:h-3 rounded-full bg-[#391C43]" />

            <p className="text-[11px] sm:text-base font-medium text-stone-800 leading-tight">
              {step.title}
            </p>

            <p className="text-[9px] sm:text-sm text-stone-500 leading-tight">
              {step.copy}
            </p>

          </li>
        ))}
      </ol>
    </div>

    {/* Right */}
    <div className="flex justify-center">
      <img
        src="/expect1.png"
        alt=""
        className="w-full sm:w-[80%] h-auto object-contain"
      />
    </div>

  </div>
</section>

        {/* ---------- Reviews ---------- */}
        <section className="mt-16">
          {/* Centered Heading */}
          <h2 className="text-2xl lg:text-3xl font-cormorant text-center">
            Customer Reviews
          </h2>

          {/* Centered Rating */}
          <div className="text-center mt-2">
            <p className="text-[#C99A3C] text-lg">★4.7/5</p>

            <p className="text-sm text-stone-500 underline underline-offset-2 mt-1">
              Based on 100 reviews
            </p>

            <button
              onClick={() => setShowReviewModal(true)}
              className="mt-4 bg-[#391C43] text-white rounded-full px-6 py-2 text-sm"
            >
              Write a review
            </button>
          </div>

          {/* Reviews Content */}
          <div className="max-w-xl mx-auto lg:mx-0 mt-8">
            {/* Review Photos */}
            <div className="flex gap-2">
              {[1, 2, 3, 4].map((i) => (
                <ImgPlaceholder
                  key={i}
                  label={`Photo ${i}`}
                  className="w-16 h-16 rounded-lg"
                />
              ))}

              <div className="w-16 h-16 flex items-center justify-center text-xs text-stone-500 border border-stone-200 rounded-lg">
                +5
              </div>
            </div>

            {/* Review Cards */}
            <div className="space-y-5 mt-8">
              {[1, 2].map((i) => (
                <div
                  key={i}
                  className="border border-stone-200 rounded-xl p-5"
                >
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-full bg-stone-200" />

                    <div>
                      <p className="text-sm font-medium text-stone-800">
                        Username
                      </p>

                      <p className="text-[#C99A3C] text-sm">
                        ★★★★★
                      </p>
                    </div>
                  </div>

                  <p className="text-sm text-stone-600 mt-3">
                    No complicated wellness plans. Just one gummy and I'm good to go.
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>

      {showReviewModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
          <div className="relative w-[500px] max-w-full h-[600px] max-h-[90vh] rounded-2xl bg-white px-6 py-5 shadow-xl overflow-y-auto">

            <button
              onClick={() => setShowReviewModal(false)}
              className="absolute right-5 top-5 text-2xl text-stone-500 hover:text-stone-700"
            >
              ×
            </button>

            <h2 className="text-2xl font-outfit font-semibold text-center text-[#391C43]">
              How would you like to
              <br />
              rate this product?
            </h2>

            {/* Stars */}
            <div className="flex justify-center gap-2 mt-6">
              {[1, 2, 3, 4, 5].map((star) => (
                <button
                  key={star}
                  className="text-4xl text-stone-300 hover:text-[#F5B301] transition-colors"
                >
                  ☆
                </button>
              ))}
            </div>

            {/* Product Image */}
            <div className="flex justify-center mt-8">
              <img
                src="/sleep-gummies.png"
                alt="Sleep Gummies"
                className="w-28 h-28 object-contain"
              />
            </div>

            {/* Product Title */}
            <h3 className="mt-4 text-center text-lg font-medium text-stone-800">
              Wellvia Sleep Gummies
            </h3>

            {/* Review Box */}
            <textarea
              placeholder="Leave a review..."
              rows={5}
              className="mt-6 w-full rounded-xl border border-stone-300 p-4 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-[#391C43]"
            />

            {/* Submit */}
            <button className="mt-6 w-full rounded-full bg-[#391C43] py-3 text-white font-medium hover:opacity-90 transition">
              Submit Review
            </button>
          </div>
        </div>
      )}
    </Page>
  );
}