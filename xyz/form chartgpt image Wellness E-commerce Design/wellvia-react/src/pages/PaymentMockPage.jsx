import { useNavigate } from 'react-router-dom';
import { LockIcon } from '../components/Icons';

const inputCls = 'w-full bg-bg border border-line rounded-xl px-4 py-3.5 text-[14px]';

/**
 * PaymentMockPage — a stand-in hosted-gateway screen (e.g. Razorpay).
 * In production you'd redirect to the real gateway; this mimics it so the
 * full flow is demoable. "Pay" routes to /payment/return.
 */
export default function PaymentMockPage() {
  const navigate = useNavigate();
  return (
    <main className="paper min-h-screen flex items-start justify-center pt-[clamp(40px,8vh,90px)] px-6 pb-16">
      <div className="w-full max-w-[440px] bg-card border border-line rounded-xl3 overflow-hidden shadow-[0_24px_60px_-30px_rgba(40,30,10,0.4)] animate-rise">
        <div className="bg-green text-[#f3efe6] px-6 py-[18px] flex items-center justify-between">
          <div className="flex items-center gap-2 text-[14px]"><LockIcon size={16} stroke="#f3efe6" />Secure Payment</div>
          <span className="text-[12px] text-[#f3efe6]/70">Razorpay</span>
        </div>
        <div className="px-6 py-[26px]">
          <div className="flex justify-between items-center pb-4 border-b border-line mb-[18px]">
            <div><div className="text-[12px] text-muted">Paying to</div><div className="text-[15px]">Wellvia Wellness Pvt Ltd</div></div>
            <div className="font-serif text-[26px]">₹999</div>
          </div>
          <div className="text-[11px] tracking-[0.1em] uppercase text-muted mb-2.5">Card Details</div>
          <div className="flex flex-col gap-3">
            <input placeholder="Card Number" className={inputCls} />
            <div className="grid grid-cols-2 gap-3">
              <input placeholder="MM / YY" className={inputCls} />
              <input placeholder="CVV" className={inputCls} />
            </div>
          </div>
          <button onClick={() => navigate('/payment/return')} className="w-full bg-green text-white border-0 rounded-full py-4 text-[15px] cursor-pointer mt-5 hover:bg-greenh">Pay ₹999</button>
          <div className="text-center text-[11.5px] text-muted mt-3.5">256-bit SSL encrypted · PCI-DSS compliant</div>
        </div>
      </div>
    </main>
  );
}
