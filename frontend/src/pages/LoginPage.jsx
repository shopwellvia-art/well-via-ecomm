import { useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Mail,
  Lock,
  User,
  Sparkles,
  Gift,
  ShieldCheck,
  ArrowLeft,
  Star,
  Shield,
  Truck,
  Clock,
  Package,
  BadgeCheck,
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
  clock: Clock,
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
    email: 'admin@lumen.store',
    password: 'Admin123!',
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
    <main className="flex min-h-[calc(100vh-4rem)] w-full items-stretch bg-bg-base">
      {/* ── LEFT promo panel (hidden on mobile) ── */}
      <div className="hidden w-[360px] shrink-0 flex-col justify-between bg-accent p-10 lg:flex">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="grid size-9 place-items-center rounded-sm bg-white/20">
              <Sparkles className="size-5 text-white" aria-hidden="true" />
            </span>
            <span className="text-lg font-bold text-white tracking-tight">ShopFlow</span>
          </div>

          <h2 className="mt-12 text-[2rem] font-bold leading-tight text-white">
            India&apos;s fastest <br /> growing marketplace
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-white/75">
            Millions of products. Trusted sellers. Secure payments. All in one place.
          </p>

          <ul className="mt-10 flex flex-col gap-4">
            {[
              { icon: Package, text: '2-day delivery on eligible orders' },
              { icon: Shield, text: 'Buyer protection on every order' },
              { icon: Truck, text: 'Easy returns within 10 days' },
              { icon: Star, text: 'Verified seller ratings & reviews' },
            ].map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-center gap-3 text-sm text-white/80">
                <Icon className="size-4 shrink-0 text-white" aria-hidden="true" />
                {text}
              </li>
            ))}
          </ul>
        </div>

        <p className="text-[11px] text-white/40">
          New to ShopFlow?{' '}
          <button
            type="button"
            onClick={() => { setMode('register'); setError(null); }}
            className="font-semibold text-white/70 underline underline-offset-2 hover:text-white focus-visible:focus-ring"
          >
            Create a free account
          </button>
        </p>
      </div>

      {/* ── RIGHT form panel ── */}
      <div className="flex flex-1 flex-col items-center justify-center px-6 py-10">
        <div className="w-full max-w-sm">

          {/* Mobile brand mark */}
          <div className="mb-7 flex items-center gap-2 lg:hidden">
            <span className="grid size-8 place-items-center rounded-sm bg-accent">
              <Sparkles className="size-4 text-white" aria-hidden="true" />
            </span>
            <span className="text-base font-bold text-ink-primary">ShopFlow</span>
          </div>

          {/* TOTP step */}
          {pendingTotp ? (
            <div>
              <h1 className="text-xl font-semibold text-ink-primary">Two-factor verification</h1>
              <p className="mt-1 text-sm text-ink-secondary">
                Enter the 6-digit code for{' '}
                <span className="font-medium text-ink-primary">{pendingTotp.email}</span>.
              </p>

              <div className="mt-5 rounded-sm border border-accent/25 bg-accent/5 px-4 py-3 text-xs text-ink-secondary">
                <p className="flex items-center gap-2 font-semibold text-ink-primary">
                  <ShieldCheck className="size-4 text-accent" aria-hidden="true" />
                  Open your authenticator app
                </p>
                <p className="mt-1 leading-relaxed">
                  No device? Use a backup code instead.
                </p>
              </div>

              <form onSubmit={handleTotpSubmit} className="mt-5">
                <Input
                  label="6-digit code"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="123456"
                  inputMode="text"
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
              {/* Mode toggle */}
              <div className="mb-6 flex rounded-sm border border-line-subtle bg-bg-elevated overflow-hidden">
                {['login', 'register'].map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      setMode(m);
                      setError(null);
                    }}
                    className={cn(
                      'flex-1 py-2.5 text-sm font-medium transition-colors duration-150 focus-visible:focus-ring',
                      mode === m
                        ? 'bg-accent text-white'
                        : 'text-ink-secondary hover:text-ink-primary hover:bg-bg-sunken',
                    )}
                  >
                    {m === 'login' ? 'Login' : 'New Customer? Sign up'}
                  </button>
                ))}
              </div>

              <h1 className="text-xl font-semibold text-ink-primary">
                {isRegister ? 'Create account' : 'Login'}
              </h1>
              <p className="mt-0.5 text-xs text-ink-secondary">
                {isRegister
                  ? 'Get access to your Orders, Wishlist and Recommendations'
                  : 'Get access to your Orders, Wishlist and Recommendations'}
              </p>

              {/* Referral banner */}
              {pendingReferral && (
                <div className="mt-4 flex items-start gap-2.5 rounded-sm border border-accent/25 bg-accent/5 px-3 py-2.5 text-xs text-ink-primary">
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
              <form onSubmit={handleSubmit} className="mt-5 flex flex-col gap-0.5">
                {isRegister && (
                  <Input
                    label="Full name"
                    icon={User}
                    placeholder="Jane Doe"
                    autoComplete="name"
                    value={form.full_name}
                    onChange={set('full_name')}
                  />
                )}
                <Input
                  label="Email"
                  type="email"
                  icon={Mail}
                  placeholder="you@example.com"
                  autoComplete="email"
                  required
                  value={form.email}
                  onChange={set('email')}
                />
                <Input
                  label="Password"
                  type="password"
                  icon={Lock}
                  placeholder="••••••••"
                  autoComplete={isRegister ? 'new-password' : 'current-password'}
                  required
                  value={form.password}
                  onChange={set('password')}
                  error={error}
                  helper={isRegister ? 'At least 8 characters.' : undefined}
                />

                {!isRegister && (
                  <div className="-mt-1 mb-3 text-right">
                    <Link
                      to="/forgot-password"
                      className="rounded-xs text-xs text-accent transition-colors hover:text-accent-hover focus-visible:focus-ring"
                    >
                      Forgot password?
                    </Link>
                  </div>
                )}

                <Button type="submit" block size="lg" loading={busy} className="mt-2">
                  {isRegister ? 'Create account' : 'Login'}
                </Button>
              </form>

              {/* Google OAuth */}
              {googleEnabled && (
                <div>
                  <div className="my-5 flex items-center gap-3">
                    <span className="h-px flex-1 bg-line-subtle" />
                    <span className="text-xs text-ink-tertiary">or</span>
                    <span className="h-px flex-1 bg-line-subtle" />
                  </div>
                  <a
                    href={`${env.apiBaseUrl}/auth/google/login`}
                    className={cn(
                      buttonVariants({ variant: 'outline', size: 'lg', block: true }),
                    )}
                  >
                    <GoogleIcon />
                    Continue with Google
                  </a>
                </div>
              )}

              {/* Dev/test quick logins */}
              {showTestLogins && (
                <div>
                  <div className="my-5 flex items-center gap-3">
                    <span className="h-px flex-1 bg-line-subtle" />
                    <span className="text-xs text-ink-tertiary">testing only</span>
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

              {/* Trust badges */}
              <TrustBadges />
            </>
          )}

          {/* Footer link */}
          <p className="mt-6 text-center text-xs text-ink-tertiary">
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
