import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { authApi } from '@/features/auth/api.js';
import { cn } from '@/lib/utils.js';
import Logo from '@/components/storefront/Logo';
import { MailIcon, LockIcon, CheckCircle } from '@/components/storefront/Icons';

// ── Step progress dot ────────────────────────────────────────────────────────
function StepDot({ active, done }) {
  return (
    <span
      className={cn(
        'size-2 rounded-full transition-all duration-300',
        done ? 'bg-wgold' : active ? 'bg-wgreen' : 'bg-wline',
      )}
      aria-hidden="true"
    />
  );
}

// ── Wellness input field ─────────────────────────────────────────────────────
function WField({ label, error, helper, icon: Icon, ...inputProps }) {
  return (
    <div>
      {label && (
        <label className="block text-[13px] font-medium text-wink mb-1.5">
          {label}
        </label>
      )}
      <div className="relative">
        {Icon && (
          <span className="absolute left-4 top-1/2 -translate-y-1/2 text-wmuted pointer-events-none">
            <Icon size={16} />
          </span>
        )}
        <input
          className={cn(
            'w-full bg-wpaper border border-wline rounded-xl py-[15px] text-[14px] text-wink',
            'placeholder:text-wmuted focus:outline-none focus:border-wgreen transition-colors',
            Icon ? 'pl-11 pr-4' : 'px-[17px]',
          )}
          {...inputProps}
        />
      </div>
      {error && <p className="mt-1.5 text-[12px] text-red-700">{error}</p>}
      {helper && !error && (
        <p className="mt-1 text-[11px] text-wmuted">{helper}</p>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function ForgotPasswordPage() {
  const navigate = useNavigate();

  // Multi-step state: 'request' → 'reset' → 'done'
  const [step, setStep]                 = useState('request');
  const [email, setEmail]               = useState('');
  const [otp, setOtp]                   = useState('');
  const [password, setPassword]         = useState('');
  const [error, setError]               = useState(null);
  // Separate per-field errors on the reset step (finding 4)
  const [otpError, setOtpError]         = useState(null);
  const [passwordError, setPasswordError] = useState(null);
  const [busy, setBusy]                 = useState(false);

  // Step 1 — send OTP to email
  async function handleRequest(e) {
    e.preventDefault();
    setError(null);
    if (!email.trim()) {
      setError('Enter your email address.');
      return;
    }
    setBusy(true);
    try {
      await authApi.forgotPassword(email.trim());
      setStep('reset');
    } catch (err) {
      // Surface server error message instead of swallowing it (finding 1)
      setError(
        err.response?.data?.error?.message || 'Something went wrong. Please try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  // Step 2 — verify OTP + set new password
  async function handleReset(e) {
    e.preventDefault();
    setOtpError(null);
    setPasswordError(null);

    if (otp.trim().length !== 6) {
      setOtpError('Enter the 6-digit code from your email.');
      return;
    }
    if (password.length < 8) {
      setPasswordError('Password must be at least 8 characters.');
      return;
    }
    setBusy(true);
    try {
      await authApi.resetPassword(email.trim(), otp.trim(), password);
      setStep('done');
    } catch (err) {
      setPasswordError(
        err.response?.data?.error?.message ||
          'That code is invalid or has expired. Request a new one.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="paper bg-wcanvas min-h-screen flex flex-col items-center justify-start pt-[clamp(48px,10vh,110px)] px-6 pb-16">

      {/* Wordmark */}
      <div className="mb-8">
        <Logo size="md" stacked={true} />
      </div>

      {/* ── Card ──────────────────────────────────────────────────────────── */}
      <div className="w-full max-w-[420px] bg-wcard border border-wline rounded-xl3 p-7 lg:p-11 text-center animate-rise">

        {/* ── Step 3: Success ── */}
        {step === 'done' && (
          <div className="flex flex-col items-center py-4">
            <div className="w-14 h-14 rounded-full bg-wgreen/[0.12] flex items-center justify-center mx-auto mb-5">
              <CheckCircle size={28} stroke="#183A2E" strokeWidth={1.4} />
            </div>
            <h1 className="font-wserif font-medium text-[clamp(24px,3vw,32px)] text-wink mb-2">
              Password reset
            </h1>
            <p className="text-[14px] text-wmuted leading-[1.6] font-light mb-6">
              Your password has been changed. You can sign in with your new password now.
            </p>
            <button
              onClick={() => navigate('/login')}
              className="w-full bg-wgreen text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-wgreen-dark transition-colors font-medium"
            >
              Go to login
            </button>
          </div>
        )}

        {/* ── Steps 1 + 2 ── */}
        {step !== 'done' && (
          <>
            {/* Icon circle */}
            <div className="w-14 h-14 rounded-full bg-wgold/15 flex items-center justify-center mx-auto mb-5">
              {step === 'request'
                ? <MailIcon size={26} stroke="#183A2E" strokeWidth={1.4} />
                : <LockIcon size={26} stroke="#183A2E" strokeWidth={1.4} />
              }
            </div>

            {/* Heading */}
            <h1 className="font-wserif font-medium text-[clamp(28px,3.4vw,38px)] text-wink m-0 mb-2.5">
              {step === 'request' ? 'Reset your password' : 'Enter your code'}
            </h1>

            {/* Description */}
            <p className="text-[14px] text-wmuted leading-[1.6] m-0 mb-5 font-light">
              {step === 'request'
                ? "Enter your email and we'll send you a 6-digit code to reset your password."
                : `We sent a code to ${email}. It expires in 10 minutes.`}
            </p>

            {/* Step progress indicator */}
            <div className="flex items-center justify-center gap-2 mb-6">
              <span className="sr-only">
                {step === 'request'
                  ? 'Step 1 of 2: Enter email'
                  : 'Step 2 of 2: Enter code and new password'}
              </span>
              <StepDot done={step === 'reset'} active={step === 'request'} />
              <span className="h-px w-8 bg-wline" aria-hidden="true" />
              <StepDot done={false} active={step === 'reset'} />
            </div>

            {/* ── Step 1: Email ── */}
            {step === 'request' && (
              <form onSubmit={handleRequest} className="flex flex-col gap-3 text-left">
                <WField
                  label="Email address"
                  type="email"
                  icon={MailIcon}
                  placeholder="you@example.com"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  error={error}
                />
                <button
                  type="submit"
                  disabled={busy}
                  className="w-full bg-wgreen text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-wgreen-dark transition-colors font-medium disabled:opacity-60 mt-1"
                >
                  {busy ? 'Sending…' : 'Send reset code'}
                </button>
              </form>
            )}

            {/* ── Step 2: OTP + new password ── */}
            {step === 'reset' && (
              <form onSubmit={handleReset} className="flex flex-col gap-3 text-left">
                <WField
                  label="6-digit code"
                  type="text"
                  icon={LockIcon}
                  placeholder="123456"
                  inputMode="numeric"
                  maxLength={6}
                  autoComplete="one-time-code"
                  required
                  value={otp}
                  onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
                  error={otpError}
                />
                <WField
                  label="New password"
                  type="password"
                  icon={LockIcon}
                  placeholder="••••••••"
                  autoComplete="new-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  error={passwordError}
                  helper="At least 8 characters."
                />
                <button
                  type="submit"
                  disabled={busy}
                  className="w-full bg-wgreen text-white border-0 rounded-full py-4 text-[14.5px] cursor-pointer hover:bg-wgreen-dark transition-colors font-medium disabled:opacity-60 mt-1"
                >
                  {busy ? 'Resetting…' : 'Reset password'}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setStep('request');
                    setError(null);
                    setOtpError(null);
                    setPasswordError(null);
                    setOtp('');
                  }}
                  className="w-full text-center text-[13px] text-wmuted hover:text-wink transition-colors py-1"
                >
                  Use a different email
                </button>
              </form>
            )}
          </>
        )}
      </div>

      {/* Back to login — visible on steps 1 + 2 */}
      {step !== 'done' && (
        <p className="text-[13px] text-wmuted mt-6">
          <Link
            to="/login"
            className="text-wgreen no-underline hover:text-wgreen-dark transition-colors"
          >
            ← Back to sign in
          </Link>
        </p>
      )}
    </main>
  );
}
