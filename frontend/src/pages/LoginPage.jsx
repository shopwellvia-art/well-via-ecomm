import { useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Mail,
  Lock,
  User,
  ShieldCheck,
  ArrowLeft,
  Star,
  Shield,
  Truck,
  Package,
  BadgeCheck,
  Sparkles,
  Gift,
} from 'lucide-react';
import { Input } from '@/components/ui/Input.jsx';
import { Button, buttonVariants } from '@/components/ui/Button.jsx';
import { authApi } from '@/features/auth/api.js';
import { useAuthStore } from '@/features/auth/store.js';
import {
  clearPendingReferralCode,
  getPendingReferralCode,
} from '@/features/loyalty/referralCapture.js';
import { cn } from '@/lib/utils.js';
import { env } from '@/config/env.js';
import { usePublicSettings } from '@/features/settings/public.js';

// Allowlisted icon keys for trust badges.
const TRUST_ICONS = {
  star: Star,
  shield: Shield,
  truck: Truck,
  package: Package,
  badge: BadgeCheck,
};

function TrustBadges() {
  const { data: cfg } = usePublicSettings();
  if (!cfg) return null;
  const badges = [1, 2, 3, 4]
    .map((n) => ({
      label: (cfg[`login.trust_badge_${n}_label`] || '').trim(),
      iconKey: (cfg[`login.trust_badge_${n}_icon`] || 'badge').trim(),
    }))
    .filter((b) => b.label);
  if (badges.length === 0) return null;
  return (
    <div className="mt-6 grid grid-cols-2 gap-2">
      {badges.map((b, i) => {
        const Icon = TRUST_ICONS[b.iconKey] || BadgeCheck;
        return (
          <div
            key={i}
            className="flex items-center gap-2 rounded-sm border border-line-subtle bg-bg-sunken px-2.5 py-1.5 text-[11px] text-ink-secondary"
          >
            <Icon className="size-3.5 shrink-0 text-accent" aria-hidden="true" />
            <span className="truncate">{b.label}</span>
          </div>
        );
      })}
    </div>
  );
}

const TEST_ACCOUNTS = [
  {
    label: 'Admin',
    email: 'vinay@gmail.com',
    password: 'vinay@123',
    icon: ShieldCheck,
  },
  {
    label: 'Customer',
    email: 'customer@lumen.store',
    password: 'Customer123!',
    icon: User,
  },
];

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" className="size-5" aria-hidden="true">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}

// Yellow check SVG matching the design mock.
function YellowCheck() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="#FFE11B"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

export default function LoginPage() {
  const [mode, setMode] = useState('login'); // 'login' | 'register'
  const [form, setForm] = useState({ email: '', password: '', full_name: '' });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [pendingTotp, setPendingTotp] = useState(null); // { token, email }
  const [totpCode, setTotpCode] = useState('');
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const nextUrl = search.get('next');
  const setSession = useAuthStore((s) => s.setSession);
  const setUser = useAuthStore((s) => s.setUser);
  const currentUser = useAuthStore((s) => s.user);

  const { data: authConfig } = useQuery({
    queryKey: ['auth-config'],
    queryFn: authApi.getConfig,
    staleTime: Infinity,
  });
  const googleEnabled = Boolean(authConfig?.google_login);
  const showTestLogins = env.appEnv !== 'production';

  const isRegister = mode === 'register';
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const pendingReferral = isRegister ? getPendingReferralCode() : null;

  async function finishLogin(tokens, emailHint) {
    setSession({
      user: { email: emailHint },
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
    });
    let profile = null;
    try {
      profile = await authApi.me();
      setUser(profile);
    } catch {
      /* profile is non-critical for storefront use */
    }
    const safeNext = nextUrl && nextUrl.startsWith('/') ? nextUrl : null;
    navigate(safeNext ?? (profile?.is_admin ? '/admin' : '/'));
  }

  async function handleSubmit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (isRegister) {
        const referralCode = getPendingReferralCode();
        await authApi.register({
          email: form.email,
          password: form.password,
          full_name: form.full_name || undefined,
          referral_code: referralCode || undefined,
        });
        clearPendingReferralCode();
      }
      const resp = await authApi.login(form.email, form.password);
      if (resp.needs_totp) {
        setPendingTotp({ token: resp.pending_token, email: form.email });
        setTotpCode('');
        return;
      }
      await finishLogin(resp, form.email);
    } catch (err) {
      setError(err.response?.data?.error?.message || 'Something went wrong. Try again.');
    } finally {
      setBusy(false);
    }
  }

  async function quickLogin(account) {
    setError(null);
    setBusy(true);
    try {
      const resp = await authApi.login(account.email, account.password);
      if (resp.needs_totp) {
        setPendingTotp({ token: resp.pending_token, email: account.email });
        setTotpCode('');
        return;
      }
      await finishLogin(resp, account.email);
    } catch (err) {
      setError(
        err.response?.data?.error?.message ||
          'Test login failed — run scripts/seed.py to create the test accounts.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleTotpSubmit(e) {
    e.preventDefault();
    setError(null);
    if (!pendingTotp) return;
    const cleaned = totpCode.trim();
    if (!cleaned) {
      setError('Enter the 6-digit code from your authenticator (or a backup code).');
      return;
    }
    setBusy(true);
    try {
      const tokens = await authApi.loginTotp(pendingTotp.token, cleaned);
      await finishLogin(tokens, pendingTotp.email);
    } catch (err) {
      setError(err.response?.data?.error?.message || "That code didn't match.");
    } finally {
      setBusy(false);
    }
  }

  if (currentUser) {
    const safeNext = nextUrl && nextUrl.startsWith('/') ? nextUrl : '/';
    return <Navigate to={safeNext} replace />;
  }

  return (
    <main className="grid min-h-[calc(100vh-200px)] place-items-center px-4 py-10">
      <div className="grid w-full max-w-3xl grid-cols-1 overflow-hidden rounded-xl bg-bg-elevated shadow-lg sm:grid-cols-[40%_1fr]">

        {/* ── LEFT brand panel ── */}
        <div
          className="flex flex-col justify-between gap-8 p-8"
          style={{ background: 'linear-gradient(160deg,#1f63d6,#2874F0)' }}
        >
          <div>
            {/* Brand mark */}
            <div className="mb-6 flex items-center gap-2">
              <span className="grid size-8 place-items-center rounded-sm bg-white/20">
                <Sparkles className="size-4 text-white" aria-hidden="true" />
              </span>
              <span className="text-base font-bold text-white">ShopWell</span>
            </div>

            <h2 className="text-2xl font-bold leading-tight text-white">
              {isRegister ? 'Join ShopWell' : 'Login'}
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-white/85">
              {isRegister
                ? 'Create your account to start shopping with exclusive member benefits.'
                : 'Get access to your orders, wishlist and personalised recommendations.'}
            </p>
          </div>

          <ul className="space-y-3 text-sm text-white/90">
            <li className="flex items-center gap-2.5">
              <YellowCheck />
              Faster checkout
            </li>
            <li className="flex items-center gap-2.5">
              <YellowCheck />
              Order tracking
            </li>
            <li className="flex items-center gap-2.5">
              <YellowCheck />
              Exclusive member deals
            </li>
          </ul>

          {/* Toggle hint at the bottom of the panel */}
          <p className="text-[11px] text-white/50">
            {isRegister ? 'Already have an account?' : 'New to ShopWell?'}{' '}
            <button
              type="button"
              onClick={() => { setMode(isRegister ? 'login' : 'register'); setError(null); }}
              className="font-semibold text-white/80 underline underline-offset-2 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
            >
              {isRegister ? 'Sign in' : 'Create a free account'}
            </button>
          </p>
        </div>

        {/* ── RIGHT form panel ── */}
        <div className="p-8">

          {/* TOTP step */}
          {pendingTotp ? (
            <div>
              <h1 className="text-xl font-semibold text-ink-primary">Two-factor verification</h1>
              <p className="mt-1 text-sm text-ink-secondary">
                Enter the 6-digit code for{' '}
                <span className="font-medium text-ink-primary">{pendingTotp.email}</span>.
              </p>

              <div className="mt-5 rounded-sm border border-accent/20 bg-accent/12 px-4 py-3 text-xs text-ink-secondary">
                <p className="flex items-center gap-2 font-semibold text-ink-primary">
                  <ShieldCheck className="size-4 text-accent" aria-hidden="true" />
                  Open your authenticator app
                </p>
                <p className="mt-1 leading-relaxed">No device? Use a backup code instead.</p>
              </div>

              <form onSubmit={handleTotpSubmit} className="mt-5">
                <Input
                  label="6-digit code"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="123456"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  autoComplete="one-time-code"
                  error={error}
                  autoFocus
                  required
                />
                <Button type="submit" block size="lg" loading={busy} className="mt-2">
                  Verify and sign in
                </Button>
                <button
                  type="button"
                  onClick={() => {
                    setPendingTotp(null);
                    setTotpCode('');
                    setError(null);
                  }}
                  className="mt-3 inline-flex items-center gap-1 text-xs text-ink-tertiary hover:text-ink-secondary focus-visible:focus-ring"
                >
                  <ArrowLeft className="size-3" aria-hidden="true" /> Use a different account
                </button>
              </form>
            </div>
          ) : (
            <>
              {/* Mode toggle tabs */}
              <div
                role="tablist"
                aria-label="Authentication mode"
                className="mb-6 flex overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated"
                onKeyDown={(e) => {
                  const tabs = ['login', 'register'];
                  const idx = tabs.indexOf(mode);
                  if (e.key === 'ArrowRight') {
                    const next = tabs[(idx + 1) % tabs.length];
                    setMode(next);
                    setError(null);
                  } else if (e.key === 'ArrowLeft') {
                    const prev = tabs[(idx - 1 + tabs.length) % tabs.length];
                    setMode(prev);
                    setError(null);
                  }
                }}
              >
                {['login', 'register'].map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="tab"
                    aria-selected={mode === m}
                    onClick={() => {
                      setMode(m);
                      setError(null);
                    }}
                    className={cn(
                      'flex-1 py-3 text-sm font-medium transition-colors duration-150 focus-visible:focus-ring',
                      mode === m
                        ? 'bg-accent text-white'
                        : 'text-ink-secondary hover:bg-bg-sunken hover:text-ink-primary',
                    )}
                  >
                    {m === 'login' ? 'Login' : 'New Customer? Sign up'}
                  </button>
                ))}
              </div>

              {/* Referral banner */}
              {pendingReferral && (
                <div className="mb-4 flex items-start gap-2.5 rounded-sm border border-accent/20 bg-accent/12 px-3 py-2.5 text-xs text-ink-primary">
                  <Gift className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
                  <span>
                    You were invited by a friend. Sign up to claim your{' '}
                    <strong>welcome reward</strong>.
                    <span className="ml-1 font-mono text-[10px] text-ink-tertiary">
                      {pendingReferral}
                    </span>
                  </span>
                </div>
              )}

              {/* Main form */}
              <form onSubmit={handleSubmit} className="flex flex-col gap-4">
                {isRegister && (
                  <label className="block">
                    <span className="text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                      Full name
                    </span>
                    <input
                      type="text"
                      placeholder="Jane Doe"
                      autoComplete="name"
                      value={form.full_name}
                      onChange={set('full_name')}
                      className="mt-1.5 h-11 w-full rounded-lg border border-line-strong bg-bg-sunken px-3.5 text-sm outline-none transition-colors focus:border-accent"
                    />
                  </label>
                )}

                <label className="block">
                  <span className="text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Email
                  </span>
                  <input
                    type="email"
                    placeholder="you@example.com"
                    autoComplete="email"
                    required
                    value={form.email}
                    onChange={set('email')}
                    className="mt-1.5 h-11 w-full rounded-lg border border-line-strong bg-bg-sunken px-3.5 text-sm outline-none transition-colors focus:border-accent"
                  />
                </label>

                <label className="block">
                  <span className="text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                    Password
                  </span>
                  <input
                    type="password"
                    placeholder="••••••••"
                    autoComplete={isRegister ? 'new-password' : 'current-password'}
                    required
                    value={form.password}
                    onChange={set('password')}
                    className="mt-1.5 h-11 w-full rounded-lg border border-line-strong bg-bg-sunken px-3.5 text-sm outline-none transition-colors focus:border-accent"
                  />
                  {isRegister && (
                    <p className="mt-1 text-[11px] text-ink-tertiary">At least 8 characters.</p>
                  )}
                </label>

                {!isRegister && (
                  <div className="-mt-2 text-right">
                    <Link
                      to="/forgot-password"
                      className="rounded-xs text-xs text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
                    >
                      Forgot password?
                    </Link>
                  </div>
                )}

                <p className="text-[11px] leading-relaxed text-ink-tertiary">
                  By continuing, you agree to ShopWell&apos;s{' '}
                  <a href="#" className="text-accent hover:underline">
                    Terms of Use
                  </a>{' '}
                  and{' '}
                  <a href="#" className="text-accent hover:underline">
                    Privacy Policy
                  </a>
                  .
                </p>

                {error && (
                  <div
                    role="alert"
                    className="rounded-sm border border-danger/25 bg-danger/12 px-3 py-2 text-xs text-danger"
                  >
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={busy}
                  aria-busy={busy}
                  className={cn(
                    'flex h-11 w-full items-center justify-center gap-2 rounded-lg bg-cta text-sm font-bold uppercase tracking-wide text-white shadow-sm',
                    'transition-transform hover:-translate-y-0.5 active:translate-y-0',
                    'focus-visible:focus-ring disabled:opacity-50 disabled:pointer-events-none',
                  )}
                >
                  {busy ? (
                    <svg className="size-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : isRegister ? (
                    'Create account'
                  ) : (
                    'Login'
                  )}
                </button>
              </form>

              {/* Google OAuth */}
              {googleEnabled && (
                <div>
                  <div className="my-4 flex items-center gap-3 text-xs text-ink-tertiary">
                    <span className="h-px flex-1 bg-line-subtle" />
                    OR
                    <span className="h-px flex-1 bg-line-subtle" />
                  </div>
                  <a
                    href={`${env.apiBaseUrl}/auth/google/login`}
                    className={cn(
                      buttonVariants({ variant: 'outline', size: 'md', block: true }),
                    )}
                  >
                    <GoogleIcon />
                    Continue with Google
                  </a>
                </div>
              )}

              {/* OTP button */}
              <div className="mt-3">
                <div className="flex items-center gap-3 text-xs text-ink-tertiary">
                  <span className="h-px flex-1 bg-line-subtle" />
                  OR
                  <span className="h-px flex-1 bg-line-subtle" />
                </div>
                <button
                  type="button"
                  className="mt-3 h-11 w-full rounded-lg border border-line-strong bg-bg-elevated text-sm font-semibold text-ink-primary transition-colors hover:border-accent hover:text-accent focus-visible:focus-ring"
                >
                  Request OTP on email
                </button>
              </div>

              {/* Dev/test quick logins */}
              {showTestLogins && (
                <div>
                  <div className="my-4 flex items-center gap-3 text-xs text-ink-tertiary">
                    <span className="h-px flex-1 bg-line-subtle" />
                    testing only
                    <span className="h-px flex-1 bg-line-subtle" />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {TEST_ACCOUNTS.map((account) => (
                      <Button
                        key={account.email}
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => quickLogin(account)}
                      >
                        <account.icon className="size-4" aria-hidden="true" />
                        {account.label}
                      </Button>
                    ))}
                  </div>
                </div>
              )}

              {/* Trust badges from CMS */}
              <TrustBadges />

              {/* Create account link */}
              <p className="mt-5 text-center text-sm text-ink-secondary">
                {isRegister ? 'Already have an account? ' : 'New to ShopWell? '}
                <button
                  type="button"
                  onClick={() => { setMode(isRegister ? 'login' : 'register'); setError(null); }}
                  className="font-semibold text-accent hover:underline focus-visible:focus-ring"
                >
                  {isRegister ? 'Sign in' : 'Create an account'}
                </button>
              </p>
            </>
          )}

          {/* Footer link */}
          <p className="mt-5 text-center text-xs text-ink-tertiary">
            <Link
              to="/products"
              className="rounded-xs transition-colors hover:text-ink-secondary focus-visible:focus-ring"
            >
              Continue browsing without signing in
            </Link>
          </p>
        </div>
      </div>
    </main>
  );
}
