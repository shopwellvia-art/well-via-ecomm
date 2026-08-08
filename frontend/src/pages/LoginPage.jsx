import { useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { authApi } from '@/features/auth/api.js';
import { useAuthStore } from '@/features/auth/store.js';
import {
  clearPendingReferralCode,
  getPendingReferralCode,
} from '@/features/loyalty/referralCapture.js';
import { cn } from '@/lib/utils.js';
import { env } from '@/config/env.js';
import { usePublicSettings } from '@/features/settings/public.js';
import { trackRegistration } from '@/features/tracking/metaPixel.js';
import { LeafMark } from '@/components/storefront/Logo.jsx';
import {
  ShieldIcon,
  UserIcon,
  MailIcon,
  LeafIcon,
  TruckIcon,
} from '@/components/storefront/Icons.jsx';

// Trust icon map using the storefront inline icon set
const TRUST_ICONS = {
  star: LeafIcon,
  shield: ShieldIcon,
  truck: TruckIcon,
  package: LeafIcon,
  badge: ShieldIcon,
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
        const Icon = TRUST_ICONS[b.iconKey] || ShieldIcon;
        return (
          <div
            key={i}
            className="flex items-center gap-2 rounded-xl border border-wline bg-wcard px-2.5 py-1.5 text-[11px] text-wmuted"
          >
            <Icon size={14} stroke="#B49A63" />
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
    icon: ShieldIcon,
  },
  {
    label: 'Customer',
    email: 'customer@lumen.store',
    password: 'Customer123!',
    icon: UserIcon,
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

// Shared input style — wellness skin, no @/components/ui dependency
const inputCls =
  'w-full bg-wcard border border-wline rounded-xl px-[17px] py-[15px] text-[14px] text-wink ' +
  'placeholder:text-wmuted outline-none transition-colors focus:border-wgreen font-wsans';

/**
 * Only honour `next` when it is an internal path: exactly one leading "/".
 * "//host" is a protocol-relative EXTERNAL url (and browsers normalise
 * "/\host" to it); react-router's pushState fails cross-origin and falls back
 * to window.location.assign(), which would make this an open redirect.
 */
export function sanitizeNext(raw) {
  return raw && /^\/(?![/\\])/.test(raw) ? raw : null;
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
  // Gate on Vite's compile-time DEV flag, not a runtime env value: `false` in
  // any production build lets Vite dead-code-eliminate the block below AND the
  // TEST_ACCOUNTS credentials, so they never ship in the bundle. (The old
  // runtime check still compiled the passwords into the shipped JS.)
  const showTestLogins = import.meta.env.DEV;

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
    const safeNext = sanitizeNext(nextUrl);
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
        // Meta `CompleteRegistration` — after the account exists, before the
        // login that follows it, so a login failure cannot un-create the account
        // and leave the event unsent.
        trackRegistration();
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
    const safeNext = sanitizeNext(nextUrl) ?? '/';
    return <Navigate to={safeNext} replace />;
  }

  return (
    <main className="paper grid md:grid-cols-2 min-h-screen bg-wcanvas">

      {/* ── LEFT: form column ── */}
      <div className="flex items-center justify-center px-6 sm:px-10 lg:px-14 py-9 lg:py-[68px]">
        <div className="w-full max-w-[380px] animate-rise">

          {/* Wordmark */}
          <Link to="/" className="inline-flex items-center gap-2 no-underline mb-8">
            <LeafMark size={30} dot={false} />
            <span className="font-display text-[18px] tracking-[0.2em] font-medium text-wgreen pl-[0.2em]">
              WELLVIA
            </span>
          </Link>

          {/* ── TOTP step ── */}
          {pendingTotp ? (
            <div>
              <div className="text-[11px] tracking-[0.24em] uppercase text-wgold mb-3.5">
                Two-factor verification
              </div>
              <h1 className="font-wserif font-medium text-[clamp(32px,3.5vw,42px)] leading-[1.05] text-wink mb-2">
                Confirm your identity
              </h1>
              <p className="text-[14px] text-wmuted font-light mb-6">
                Enter the 6-digit code for{' '}
                <span className="font-medium text-wink">{pendingTotp.email}</span>.
              </p>

              <div className="rounded-xl border border-wline bg-wcard px-4 py-3.5 mb-5 text-xs text-wmuted">
                <p className="flex items-center gap-2 font-semibold text-wink mb-1">
                  <ShieldIcon size={15} stroke="#183A2E" />
                  Open your authenticator app
                </p>
                <p className="leading-relaxed">No device? Use a backup code instead.</p>
              </div>

              <form onSubmit={handleTotpSubmit} className="flex flex-col gap-4">
                <div>
                  <label className="text-[11px] tracking-[0.18em] uppercase text-wmuted font-medium block mb-1.5">
                    6-digit code
                  </label>
                  <input
                    value={totpCode}
                    onChange={(e) => setTotpCode(e.target.value)}
                    placeholder="123456"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    autoComplete="one-time-code"
                    autoFocus
                    required
                    className={inputCls}
                  />
                </div>

                {error && (
                  <div
                    role="alert"
                    className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-600"
                  >
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={busy}
                  aria-busy={busy}
                  className="w-full bg-wgreen text-white rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer hover:bg-wgreen-dark transition-colors disabled:opacity-60 disabled:pointer-events-none flex items-center justify-center gap-2"
                >
                  {busy ? (
                    <svg className="size-4 animate-spin360" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    'Verify and sign in'
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setPendingTotp(null);
                    setTotpCode('');
                    setError(null);
                  }}
                  className="text-xs text-wmuted hover:text-wink transition-colors focus:outline-none text-center"
                >
                  ← Use a different account
                </button>
              </form>
            </div>
          ) : (
            <>
              {/* Mode tabs — Login / Create Account */}
              <div
                role="tablist"
                aria-label="Authentication mode"
                className="mb-7 flex overflow-hidden rounded-full border border-wline bg-wcard p-0.5"
                onKeyDown={(e) => {
                  const tabs = ['login', 'register'];
                  const idx = tabs.indexOf(mode);
                  if (e.key === 'ArrowRight') {
                    setMode(tabs[(idx + 1) % tabs.length]);
                    setError(null);
                  } else if (e.key === 'ArrowLeft') {
                    setMode(tabs[(idx - 1 + tabs.length) % tabs.length]);
                    setError(null);
                  }
                }}
              >
                {[
                  { key: 'login', label: 'Sign In' },
                  { key: 'register', label: 'Create Account' },
                ].map(({ key, label }) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={mode === key}
                    onClick={() => {
                      setMode(key);
                      setError(null);
                    }}
                    className={cn(
                      'flex-1 py-2.5 text-[13px] font-medium transition-all duration-200 focus:outline-none rounded-full',
                      mode === key
                        ? 'bg-[#08112C] text-white shadow-sm'
                        : 'text-wmuted hover:text-wink',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {/* Eyebrow + editorial heading */}
              <div className="text-[11px] tracking-[0.24em] uppercase text-wgold mb-3.5">
                {isRegister ? 'New member' : 'Welcome back'}
              </div>
              <h1 className="font-wserif font-medium text-[clamp(34px,4vw,46px)] leading-[1.05] text-wink m-0 mb-2">
                {isRegister ? 'Begin your ritual' : 'Sign in to your ritual'}
              </h1>
              <p className="text-[14px] text-wmuted m-0 mb-7 font-light">
                {isRegister
                  ? 'Create your account to unlock exclusive wellness rewards.'
                  : 'Track orders, manage subscriptions and earn rewards.'}
              </p>

              {/* Referral banner — only in register mode when a code is pending */}
              {pendingReferral && (
                <div className="mb-5 flex items-start gap-2.5 rounded-xl border border-wgold/30 bg-wcard px-3 py-2.5 text-xs text-wink">
                  <LeafIcon size={14} stroke="#B49A63" className="mt-0.5 shrink-0" />
                  <span>
                    You were invited by a friend. Sign up to claim your{' '}
                    <strong>welcome reward</strong>.
                    <span className="ml-1 font-mono text-[10px] text-wmuted">
                      {pendingReferral}
                    </span>
                  </span>
                </div>
              )}

              {/* Main auth form */}
              <form onSubmit={handleSubmit} className="flex flex-col gap-3.5">
                {isRegister && (
                  <div>
                    <label className="text-[11px] tracking-[0.18em] uppercase text-wmuted font-medium block mb-1.5">
                      Full name
                    </label>
                    <input
                      type="text"
                      placeholder="Jane Doe"
                      autoComplete="name"
                      value={form.full_name}
                      onChange={set('full_name')}
                      className={inputCls}
                    />
                  </div>
                )}

                <div>
                  <label className="text-[11px] tracking-[0.18em] uppercase text-wmuted font-medium block mb-1.5">
                    Email address
                  </label>
                  <input
                    type="email"
                    placeholder="you@example.com"
                    autoComplete="email"
                    required
                    value={form.email}
                    onChange={set('email')}
                    className={inputCls}
                  />
                </div>

                <div>
                  <label className="text-[11px] tracking-[0.18em] uppercase text-wmuted font-medium block mb-1.5">
                    Password
                  </label>
                  <input
                    type="password"
                    placeholder="••••••••"
                    autoComplete={isRegister ? 'new-password' : 'current-password'}
                    required
                    value={form.password}
                    onChange={set('password')}
                    className={inputCls}
                  />
                  {isRegister && (
                    <p className="mt-1 text-[11px] text-wmuted">At least 8 characters.</p>
                  )}
                </div>

                {/* Remember me + forgot password row */}
                <div className="flex justify-between items-center text-[12.5px] text-wmuted -mt-1">
                  <label className="flex items-center gap-2 cursor-pointer select-none">
                    <input type="checkbox" className="accent-wgreen" />
                    Remember me
                  </label>
                  {!isRegister && (
                    <Link
                      to="/forgot-password"
                      className="text-[#08112C] no-underline hover:underline hover:underline-offset-2 transition-colors"
                    >
                      Forgot password?
                    </Link>
                  )}
                </div>

                <p className="text-[11px] leading-relaxed text-wmuted">
                  By continuing, you agree to Wellvia&apos;s{' '}
                  <a href="#" className="text-wgreen hover:underline">
                    Terms of Use
                  </a>{' '}
                  and{' '}
                  <a href="#" className="text-wgreen hover:underline">
                    Privacy Policy
                  </a>
                  .
                </p>

                {error && (
                  <div
                    role="alert"
                    className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-600"
                  >
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={busy}
                  aria-busy={busy}
                  className="w-full bg-[#08112C] text-white border-0 rounded-full py-4 text-[14.5px] tracking-wide cursor-pointer hover:bg-[#08112C] transition-colors disabled:opacity-60 disabled:pointer-events-none flex items-center justify-center gap-2 mt-1"
                >
                  {busy ? (
                    <svg className="size-4 animate-spin360" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : isRegister ? (
                    'Create account'
                  ) : (
                    'Sign In'
                  )}
                </button>
              </form>

              {/* Google OAuth — only when backend reports it as enabled */}
              {googleEnabled && (
                <div>
                  <div className="flex items-center gap-3.5 my-[22px] text-wmuted text-[12px]">
                    <span className="flex-1 h-px bg-wline" />
                    or continue with
                    <span className="flex-1 h-px bg-wline" />
                  </div>
                  <a
                    href={`${env.apiBaseUrl}/auth/google/login`}
                    className="flex w-full items-center justify-center gap-2 rounded-full border border-wline bg-wcard py-3.5 text-[13px] text-wink hover:border-wgreen transition-colors no-underline"
                  >
                    <GoogleIcon />
                    Continue with Google
                  </a>
                </div>
              )}

              {/* OTP email button — placeholder, no handler yet */}
              <div className="mt-4">
                <div className="flex items-center gap-3.5 mb-4 text-wmuted text-[12px]">
                  <span className="flex-1 h-px bg-wline" />
                  or
                  <span className="flex-1 h-px bg-wline" />
                </div>
                <button
                  type="button"
                  className="w-full flex items-center justify-center gap-2 rounded-full border border-wline bg-wcard py-3.5 text-[13px] text-wink hover:border-wgreen transition-colors"
                >
                  <MailIcon size={15} />
                  Request OTP on email
                </button>
              </div>

              {/* Dev / test quick-login buttons */}
              {showTestLogins && (
                <div>
                  <div className="my-4 flex items-center gap-3 text-[11px] text-wmuted">
                    <span className="h-px flex-1 bg-wline" />
                    testing only
                    <span className="h-px flex-1 bg-wline" />
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    {TEST_ACCOUNTS.map((account) => (
                      <button
                        key={account.email}
                        type="button"
                        disabled={busy}
                        onClick={() => quickLogin(account)}
                        className="flex items-center justify-center gap-1.5 rounded-xl border border-wline bg-wcard px-3 py-2.5 text-[12px] text-wink hover:border-wgreen transition-colors disabled:opacity-50"
                      >
                        <account.icon size={14} />
                        {account.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Trust badges sourced from CMS / public settings */}
              <TrustBadges />

              {/* Mode toggle — bottom link */}
              <p className="text-center text-[13px] text-wmuted mt-6">
                {isRegister ? 'Already have an account? ' : 'New to Wellvia? '}
                <button
                  type="button"
                  onClick={() => {
                    setMode(isRegister ? 'login' : 'register');
                    setError(null);
                  }}
                  className="text-[#08112C] underline underline-offset-[3px] hover:text-[#08112C] focus:outline-none transition-colors"
                >
                  {isRegister ? 'Sign in' : 'Create an account'}
                </button>
              </p>
            </>
          )}

          {/* Skip sign-in */}
          <p className="mt-5 text-center text-[12px] text-wmuted">
            <Link
              to="/products"
              className="hover:text-wink transition-colors no-underline"
            >
              Continue browsing without signing in
            </Link>
          </p>
        </div>
      </div>

      {/* ── RIGHT: decorative wellness column (hidden on mobile) ── */}
      <div
        className="relative hidden md:block"
        style={{ background: 'linear-gradient(160deg,#e7e0d3,#d9cfbd)' }}
      >
        {/* Tonal earth-tone gradient fills the column */}
        <div
          className="absolute inset-0 w-full h-full min-h-screen"
          style={{
            background:
              'linear-gradient(145deg,#dbd3c4 0%,#c9bfad 35%,#b5a790 65%,#9f9180 100%)',
          }}
        />

        {/* Subtle botanical dot pattern */}
        <div
          className="absolute inset-0 opacity-[0.05]"
          style={{
            backgroundImage: 'radial-gradient(circle,#183A2E 1px,transparent 1px)',
            backgroundSize: '32px 32px',
          }}
        />

        {/* Bottom gradient + editorial quote */}
        <div
          className="absolute inset-x-0 bottom-0 p-9 text-[#f3efe6]"
          style={{ background: 'linear-gradient(180deg,transparent,rgba(24,58,46,0.55))' }}
        >
          <p className="font-wserif italic text-[24px] leading-[1.4] m-0 max-w-[340px]">
            &ldquo;A few minutes each morning — the calmest part of my day.&rdquo;
          </p>
        </div>
      </div>

    </main>
  );
}
