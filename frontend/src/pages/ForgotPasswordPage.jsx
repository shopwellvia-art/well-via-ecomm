import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Mail, KeyRound, Lock, ArrowLeft, CheckCircle2 } from 'lucide-react';
import { Input } from '@/components/ui/Input.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { authApi } from '@/features/auth/api.js';
import { cn } from '@/lib/utils.js';

function StepDot({ active, done }) {
  return (
    <span
      className={cn(
        'size-2 rounded-full transition-all duration-300',
        done ? 'bg-success' : active ? 'bg-accent' : 'bg-line-strong',
      )}
      aria-hidden="true"
    />
  );
}

export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [step, setStep] = useState('request'); // 'request' | 'reset' | 'done'
  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

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
    } catch {
      setError('Something went wrong. Please try again.');
    } finally {
      setBusy(false);
    }
  }

  async function handleReset(e) {
    e.preventDefault();
    setError(null);
    if (otp.trim().length !== 6) {
      setError('Enter the 6-digit code from your email.');
      return;
    }
    if (password.length < 8) {
      setError('Password must be at least 8 characters.');
      return;
    }
    setBusy(true);
    try {
      await authApi.resetPassword(email.trim(), otp.trim(), password);
      setStep('done');
    } catch (err) {
      setError(
        err.response?.data?.error?.message ||
          'That code is invalid or has expired. Request a new one.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-[calc(100vh-4rem)] w-full items-center justify-center bg-bg-base px-4 py-12">
      <div className="w-full max-w-sm">
        {/* White card */}
        <div className="rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
          {/* Blue header bar */}
          <div className="rounded-t-sm bg-accent px-6 py-5">
            <div className="flex items-center gap-2.5">
              <span className="grid size-8 place-items-center rounded-sm bg-white/20">
                <KeyRound className="size-4 text-white" aria-hidden="true" />
              </span>
              <h1 className="text-base font-semibold text-white">
                {step === 'done'
                  ? 'Password reset'
                  : step === 'request'
                  ? 'Reset your password'
                  : 'Enter your code'}
              </h1>
            </div>
            {step !== 'done' && (
              <p className="mt-1.5 text-xs text-white/75">
                {step === 'request'
                  ? "We'll email you a 6-digit code to reset it."
                  : `We sent a code to ${email}. It expires in 10 minutes.`}
              </p>
            )}
          </div>

          <div className="px-6 py-6">
            {step === 'done' ? (
              /* Success state */
              <div className="flex flex-col items-center py-4 text-center">
                <span className="grid size-14 place-items-center rounded-full bg-success/10 text-success">
                  <CheckCircle2 className="size-7" aria-hidden="true" />
                </span>
                <p className="mt-4 text-sm font-medium text-ink-primary">
                  Your password has been changed.
                </p>
                <p className="mt-1 text-xs text-ink-secondary">
                  You can sign in with your new password now.
                </p>
                <Button block size="lg" className="mt-6" onClick={() => navigate('/login')}>
                  Go to login
                </Button>
              </div>
            ) : (
              <>
                {/* Step progress */}
                <div className="mb-5 flex items-center justify-center gap-2">
                  <StepDot done={step === 'reset'} active={step === 'request'} />
                  <span className="h-px w-8 bg-line-subtle" aria-hidden="true" />
                  <StepDot done={false} active={step === 'reset'} />
                </div>

                {step === 'request' ? (
                  <form onSubmit={handleRequest} className="flex flex-col gap-1">
                    <Input
                      label="Email address"
                      type="email"
                      icon={Mail}
                      placeholder="you@example.com"
                      autoComplete="email"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      error={error}
                    />
                    <Button type="submit" block size="lg" loading={busy} className="mt-2">
                      Send reset code
                    </Button>
                  </form>
                ) : (
                  <form onSubmit={handleReset} className="flex flex-col gap-1">
                    <Input
                      label="6-digit code"
                      icon={KeyRound}
                      placeholder="123456"
                      inputMode="numeric"
                      maxLength={6}
                      autoComplete="one-time-code"
                      required
                      value={otp}
                      onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
                    />
                    <Input
                      label="New password"
                      type="password"
                      icon={Lock}
                      placeholder="••••••••"
                      autoComplete="new-password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      error={error}
                      helper="At least 8 characters."
                    />
                    <Button type="submit" block size="lg" loading={busy} className="mt-2">
                      Reset password
                    </Button>
                    <button
                      type="button"
                      onClick={() => {
                        setStep('request');
                        setError(null);
                        setOtp('');
                      }}
                      className="mt-2 w-full rounded-xs text-center text-xs text-ink-tertiary transition-colors hover:text-ink-secondary focus-visible:focus-ring"
                    >
                      Use a different email
                    </button>
                  </form>
                )}
              </>
            )}
          </div>
        </div>

        {step !== 'done' && (
          <p className="mt-4 text-center text-xs text-ink-tertiary">
            <Link
              to="/login"
              className="inline-flex items-center gap-1.5 rounded-xs text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
            >
              <ArrowLeft className="size-3.5" aria-hidden="true" />
              Back to login
            </Link>
          </p>
        )}
      </div>
    </main>
  );
}
