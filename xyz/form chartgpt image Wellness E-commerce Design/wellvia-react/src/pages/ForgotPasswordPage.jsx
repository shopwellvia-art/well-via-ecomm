import { Link } from 'react-router-dom';
import { MailIcon } from '../components/Icons';

export default function ForgotPasswordPage() {
  return (
    <main className="paper min-h-screen flex items-start justify-center pt-[clamp(48px,10vh,110px)] px-6 pb-16">
      <div className="w-full max-w-[420px] bg-card border border-line rounded-xl3 p-7 lg:p-11 text-center animate-rise">
        <div className="w-14 h-14 rounded-full bg-gold/15 flex items-center justify-center mx-auto mb-5">
          <MailIcon size={26} stroke="#183A2E" strokeWidth={1.4} />
        </div>
        <h1 className="font-serif font-medium text-[clamp(28px,3.4vw,38px)] m-0 mb-2.5">Reset your password</h1>
        <p className="text-[14px] text-muted leading-[1.6] m-0 mb-6 font-light">Enter your email and we'll send you a secure link to set a new password.</p>
        <input placeholder="Email address" className="w-full bg-bg border border-line rounded-xl px-[17px] py-[15px] text-[14px] mb-4" />
        <button className="w-full bg-green text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-greenh">Send Reset Link</button>
        <p className="text-[13px] text-muted mt-5"><Link to="/login" className="text-green no-underline">← Back to sign in</Link></p>
      </div>
    </main>
  );
}
