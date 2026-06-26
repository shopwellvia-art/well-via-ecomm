import { useNavigate } from 'react-router-dom';
import { Check } from '../components/Icons';

/**
 * PaymentReturnPage — gateway redirect target. Reads status from the URL in
 * production (e.g. ?status=success); here it always shows success.
 */
export default function PaymentReturnPage() {
  const navigate = useNavigate();
  return (
    <main className="paper min-h-screen flex items-start justify-center pt-[clamp(48px,10vh,120px)] px-6 pb-16">
      <div className="w-full max-w-[480px] text-center bg-card border border-line rounded-xl3 p-9 lg:p-14 animate-rise">
        <div className="w-[78px] h-[78px] rounded-full bg-green flex items-center justify-center mx-auto mb-5">
          <Check size={38} stroke="#fff" strokeWidth={1.6} />
        </div>
        <h1 className="font-serif font-medium text-[clamp(30px,4vw,44px)] m-0 mb-2.5">Payment Successful</h1>
        <p className="text-[14.5px] text-muted leading-[1.6] m-0 mb-6 font-light">
          Thank you, Aditi. Your order <b className="text-ink font-medium">WV-10428</b> is confirmed
          and a receipt is on its way to your inbox.
        </p>
        <div className="bg-bg border border-line rounded-2xl p-[18px] flex justify-between items-center mb-6 text-left">
          <div><div className="text-[12px] text-muted">Amount Paid</div><div className="font-serif text-[22px]">₹999</div></div>
          <div className="text-right"><div className="text-[12px] text-muted">Method</div><div className="text-[14px]">Razorpay · UPI</div></div>
        </div>
        <div className="flex gap-3 justify-center flex-wrap">
          <button onClick={() => navigate('/account/orders/WV-10428')} className="bg-green text-white border-0 rounded-full px-7 py-3.5 text-[13.5px] cursor-pointer hover:bg-greenh">Track Order</button>
          <button onClick={() => navigate('/shop')} className="bg-transparent border border-line rounded-full px-7 py-3.5 text-[13.5px] cursor-pointer">Continue Shopping</button>
        </div>
      </div>
    </main>
  );
}
