import { useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { motion } from 'framer-motion';
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
import { duration, ease, heroContainer, fadeUp, staggerContainer } from '@/lib/motion.js';
import { usePublicSettings } from '@/features/settings/public.js';

// Allowlisted icon keys for trust badges. Admin picks one of these in
// Settings → Login; everything else falls back to a neutral badge.
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
    <motion.div
      variants={staggerContainer(0.05)}
      initial="hidden"
      animate="show"
      className="mt-6 grid grid-cols-2 gap-2"
    >
      {badges.map((b, i) => {
        const Icon = TRUST_ICONS[b.iconKey] || BadgeCheck;
        return (
          <motion.div
            key={i}
            variants={fadeUp}
            className="flex items-center gap-2 rounded-sm border border-line-subtle bg-bg-sunken px-2.5 py-1.5 text-[11px] text-ink-secondary"
          >
            <Icon className="size-3.5 shrink-0 text-accent" aria-hidden="true" />
            <span className="truncate">{b.label}</span>
          </motion.div>
        );
      })}
    </motion.div>
  );
}

// Seeded accounts for one-click sign-in while testing (scripts/seed.py).
// Rendered only outside production builds — see `showTestLogins` below.
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
  // 2FA challenge state. When the server tells us the account needs TOTP,
  // we hold onto the pending_token and switch the form to the code prompt.
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

  // Show a "you were referred" banner when a code is sitting in sessionStorage.
  // Visible on the register tab so the friend understands why they're signing up.
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
        // Clear the stashed code regardless — invalid codes won't help on
        // retry, and successful ones already created the referral row.
        clearPendingReferralCode();
      }
      const resp = await authApi.login(form.email, form.password);
      if (resp.needs_totp) {
        // Switch to the second-factor view; keep the email visible so the
        // user knows which account they're confirming.
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
    <main className="relative mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-content items-center justify-center px-6 py-12">
      {/* Background ambient glow */}
      <div
        aria-hidden="true"
        className="absolute left-1/2 top-1/3 -z-10 size-[520px] -translate-x-1/2 rounded-full bg-accent/12 blur-[160px]"
      />
      <div
        aria-hidden="true"
        className="absolute right-1/4 bottom-1/4 -z-10 size-[280px] rounded-full bg-accent/8 blur-[120px]"
      />

      <motion.div
        variants={heroContainer}
        initial="hidden"
        animate="show"
        className="w-full max-w-md"
      >
        {/* Card */}
        <motion.div variants={fadeUp} className="gradient-border rounded-lg">
          <div className="glass rounded-lg p-8">
            {/* Brand mark + heading */}
            <motion.div variants={fadeUp} className="flex flex-col items-center text-center">
              <span className="grid size-12 place-items-center rounded-xl bg-accent text-ink-inverse shadow-glow-sm">
                <Sparkles className="size-5" aria-hidden="true" />
              </span>
              <h1 className="mt-4 text-h2 tracking-tight text-ink-primary">
                {isRegister ? 'Create your account' : 'Welcome back'}
              </h1>
              <p className="mt-1.5 text-sm text-ink-secondary">
                {isRegister
                  ? 'Join Lumen for a faster, saved checkout.'
                  : 'Sign in to continue shopping.'}
              </p>
            </motion.div>

            {/* Mode toggle pills */}
            {!pendingTotp && (
              <motion.div variants={fadeUp} className="mt-6 flex rounded-sm bg-bg-sunken p-1">
                {['login', 'register'].map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      setMode(m);
                      setError(null);
                    }}
                    className={cn(
                      'flex-1 rounded-xs py-2 text-xs font-semibold transition-all duration-200 focus-visible:focus-ring',
                      mode === m
                        ? 'bg-bg-elevated text-ink-primary shadow-sm'
                        : 'text-ink-tertiary hover:text-ink-secondary',
                    )}
                  >
                    {m === 'login' ? 'Sign in' : 'Create account'}
                  </button>
                ))}
              </motion.div>
            )}

            {/* TOTP step */}
            {pendingTotp ? (
              <motion.form
                variants={fadeUp}
                onSubmit={handleTotpSubmit}
                className="mt-6"
              >
                <div className="mb-4 rounded-sm border border-accent/30 bg-accent/8 p-4 text-xs text-ink-secondary">
                  <p className="flex items-center gap-2 font-semibold text-ink-primary">
                    <ShieldCheck className="size-4 text-accent" aria-hidden="true" />
                    Two-factor verification
                  </p>
                  <p className="mt-1.5 leading-relaxed">
                    Open your authenticator app and enter the 6-digit code for{' '}
                    <span className="font-mono font-medium text-ink-primary">{pendingTotp.email}</span>.
                    No device? Use a backup code instead.
                  </p>
                </div>
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
                <Button type="submit" block size="lg" loading={busy} className="mt-1">
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
              </motion.form>
            ) : (
              <>
                {/* Referral banner */}
                {pendingReferral && (
                  <motion.div
                    variants={fadeUp}
                    className="mt-5 flex items-start gap-2.5 rounded-sm border border-accent/30 bg-accent/8 px-3 py-2.5 text-xs text-ink-primary"
                  >
                    <Gift className="mt-0.5 size-4 shrink-0 text-accent" aria-hidden="true" />
                    <span>
                      You were invited by a friend. Sign up to claim your{' '}
                      <strong>welcome reward</strong>.
                      <span className="ml-1 font-mono text-[10px] text-ink-tertiary">
                        {pendingReferral}
                      </span>
                    </span>
                  </motion.div>
                )}

                {/* Main form */}
                <motion.form variants={fadeUp} onSubmit={handleSubmit} className="mt-5">
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

                  <Button type="submit" block size="lg" loading={busy} className="mt-1">
                    {isRegister ? 'Create account' : 'Sign in'}
                  </Button>
                </motion.form>

                {/* Google OAuth */}
                {googleEnabled && (
                  <motion.div variants={fadeUp}>
                    <div className="my-5 flex items-center gap-3">
                      <span className="h-px flex-1 bg-line-subtle" />
                      <span className="text-xs text-ink-tertiary">or continue with</span>
                      <span className="h-px flex-1 bg-line-subtle" />
                    </div>
                    <a
                      href={`${env.apiBaseUrl}/auth/google/login`}
                      className={cn(
                        buttonVariants({ variant: 'outline', size: 'lg', block: true }),
                      )}
                    >
                      <GoogleIcon />
                      Google
                    </a>
                  </motion.div>
                )}

                {/* Dev/test quick logins — hidden in production builds */}
                {showTestLogins && (
                  <motion.div variants={fadeUp}>
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
                  </motion.div>
                )}
              </>
            )}

            {/* Trust badges */}
            {!pendingTotp && <TrustBadges />}
          </div>
        </motion.div>

        {/* Footer link */}
        <motion.p variants={fadeUp} className="mt-5 text-center text-xs text-ink-tertiary">
          <Link
            to="/products"
            className="rounded-xs transition-colors hover:text-ink-secondary focus-visible:focus-ring"
          >
            Continue browsing without signing in
          </Link>
        </motion.p>
      </motion.div>
    </main>
  );
}
