import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Mail, KeyRound, Lock, ArrowLeft, CheckCircle2 } from 'lucide-react';
import { Input } from '@/components/ui/Input.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { authApi } from '@/features/auth/api.js';
import { duration, ease, heroContainer, fadeUp } from '@/lib/motion.js';

// Step indicator dot
function StepDot({ active, done }) {
  return (
    <span
      className={cn(
        'size-2 rounded-full transition-all duration-300',
        done ? 'bg-success' : active ? 'bg-accent' : 'bg-fill-strong',
      )}
      aria-hidden="true"
    />
  );
}

import { cn } from '@/lib/utils.js';

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
    <main className="relative mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-content items-center justify-center px-6 py-12">
      {/* Background ambient */}
      <div
        aria-hidden="true"
        className="absolute left-1/2 top-1/3 -z-10 size-[480px] -translate-x-1/2 rounded-full bg-accent/10 blur-[150px]"
      />

      <motion.div
        variants={heroContainer}
        initial="hidden"
        animate="show"
        className="w-full max-w-md"
      >
        <motion.div variants={fadeUp} className="gradient-border rounded-lg">
          <div className="glass rounded-lg p-8">
            {step === 'done' ? (
              /* ── Success state ── */
              <motion.div
                variants={heroContainer}
                initial="hidden"
                animate="show"
                className="flex flex-col items-center text-center"
              >
                <motion.span
                  variants={fadeUp}
                  className="grid size-14 place-items-center rounded-full bg-success/12 text-success shadow-glow-success"
                >
                  <CheckCircle2 className="size-7" aria-hidden="true" />
                </motion.span>
                <motion.h1 variants={fadeUp} className="mt-5 text-h2 tracking-tight text-ink-primary">
                  Password reset
                </motion.h1>
                <motion.p variants={fadeUp} className="mt-1.5 text-sm text-ink-secondary">
                  Your password has been changed. You can sign in with it now.
                </motion.p>
                <motion.div variants={fadeUp} className="mt-6 w-full">
                  <Button block size="lg" onClick={() => navigate('/login')}>
                    Go to sign in
                  </Button>
                </motion.div>
              </motion.div>
            ) : (
              <>
                {/* Step progress indicators */}
                <div className="mb-6 flex items-center justify-center gap-2">
                  <StepDot done={step === 'reset'} active={step === 'request'} />
                  <span className="h-px w-6 bg-line-subtle" aria-hidden="true" />
                  <StepDot done={false} active={step === 'reset'} />
                </div>

                {/* Header */}
                <motion.div variants={fadeUp} className="flex flex-col items-center text-center">
                  <span className="grid size-12 place-items-center rounded-xl bg-accent text-ink-inverse shadow-glow-sm">
                    <KeyRound className="size-5" aria-hidden="true" />
                  </span>
                  <h1 className="mt-4 text-h2 tracking-tight text-ink-primary">
                    {step === 'request' ? 'Reset your password' : 'Enter your code'}
                  </h1>
                  <p className="mt-1.5 text-sm text-ink-secondary">
                    {step === 'request'
                      ? "We'll email you a 6-digit code to reset it."
                      : `We sent a code to ${email}. It expires in 10 minutes.`}
                  </p>
                </motion.div>

                {step === 'request' ? (
                  <motion.form variants={fadeUp} onSubmit={handleRequest} className="mt-6">
                    <Input
                      label="Email"
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
                  </motion.form>
                ) : (
                  <motion.form variants={fadeUp} onSubmit={handleReset} className="mt-6">
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
                      className="mt-3 w-full rounded-xs text-center text-xs text-ink-tertiary transition-colors hover:text-ink-secondary focus-visible:focus-ring"
                    >
                      Use a different email
                    </button>
                  </motion.form>
                )}
              </>
            )}
          </div>
        </motion.div>

        {step !== 'done' && (
          <motion.p variants={fadeUp} className="mt-5 text-center text-xs text-ink-tertiary">
            <Link
              to="/login"
              className="inline-flex items-center gap-1.5 rounded-xs transition-colors hover:text-ink-secondary focus-visible:focus-ring"
            >
              <ArrowLeft className="size-3.5" aria-hidden="true" />
              Back to sign in
            </Link>
          </motion.p>
        )}
      </motion.div>
    </main>
  );
}
