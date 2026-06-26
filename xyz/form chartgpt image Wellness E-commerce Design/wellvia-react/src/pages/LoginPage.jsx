import { Link, useNavigate } from 'react-router-dom';
import ImageSlot from '../components/ImageSlot';
import { LeafMark } from '../components/Logo';

const inputCls = 'w-full bg-card border border-line rounded-xl px-[17px] py-[15px] text-[14px]';

export default function LoginPage() {
  const navigate = useNavigate();
  return (
    <main className="paper grid md:grid-cols-2 min-h-screen">
      <div className="flex items-center justify-center px-6 sm:px-10 lg:px-14 py-9 lg:py-[68px]">
        <div className="w-full max-w-[380px] animate-rise">
          <Link to="/" className="inline-flex items-center gap-2 no-underline mb-8">
            <LeafMark size={30} dot={false} />
            <span className="font-display text-[18px] tracking-[0.2em] font-medium text-green pl-[0.2em]">WELLVIA</span>
          </Link>
          <div className="text-[11px] tracking-[0.24em] uppercase text-gold mb-3.5">Welcome back</div>
          <h1 className="font-serif font-medium text-[clamp(34px,4vw,46px)] m-0 mb-2 leading-[1.05]">Sign in to your ritual</h1>
          <p className="text-[14px] text-muted m-0 mb-7 font-light">Track orders, manage subscriptions and earn rewards.</p>

          <div className="flex flex-col gap-3.5">
            <input placeholder="Email address" className={inputCls} />
            <input type="password" placeholder="Password" className={inputCls} />
          </div>
          <div className="flex justify-between items-center my-3.5 mb-[22px] text-[12.5px] text-muted">
            <label className="flex items-center gap-2 cursor-pointer"><input type="checkbox" className="accent-green" />Remember me</label>
            <Link to="/forgot-password" className="text-green no-underline">Forgot password?</Link>
          </div>
          <button onClick={() => navigate('/auth/callback')} className="w-full bg-green text-white border-0 rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer hover:bg-greenh">Sign In</button>

          <div className="flex items-center gap-3.5 my-[22px] text-muted text-[12px]">
            <span className="flex-1 h-px bg-line" />or continue with<span className="flex-1 h-px bg-line" />
          </div>
          <div className="flex gap-3">
            <button className="flex-1 bg-card border border-line rounded-full py-3.5 text-[13px] cursor-pointer flex items-center justify-center gap-2"><span className="font-semibold text-[#4285f4]">G</span> Google</button>
            <button className="flex-1 bg-card border border-line rounded-full py-3.5 text-[13px] cursor-pointer">Apple</button>
          </div>
          <p className="text-center text-[13px] text-muted mt-6">New to Wellvia? <Link to="/account" className="text-green underline underline-offset-[3px] no-underline">Create an account</Link></p>
        </div>
      </div>

      <div className="relative hidden md:block" style={{ background: 'linear-gradient(160deg,#e7e0d3,#d9cfbd)' }}>
        <ImageSlot id="auth-img" placeholder="Drop a serene lifestyle image" className="w-full h-full min-h-screen" />
        <div className="absolute inset-x-0 bottom-0 p-9 text-[#f3efe6]" style={{ background: 'linear-gradient(180deg,transparent,rgba(24,58,46,0.55))' }}>
          <p className="font-serif italic text-[24px] leading-[1.4] m-0 max-w-[340px]">“A few minutes each morning — the calmest part of my day.”</p>
        </div>
      </div>
    </main>
  );
}
