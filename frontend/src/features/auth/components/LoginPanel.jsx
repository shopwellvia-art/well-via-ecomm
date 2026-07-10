import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { authApi } from '@/features/auth/api.js';
import { useAuthStore } from '@/features/auth/store.js';
import {
  getPendingReferralCode,
  clearPendingReferralCode,
} from '@/features/loyalty/referralCapture.js';
import { env } from '@/config/env.js';
import { cn } from '@/lib/utils.js';

const inputCls =
  'w-full bg-wpaper border border-wline rounded-xl px-4 py-3 text-[14px] text-wink placeholder:text-wmuted focus:outline-none focus:ring-1 focus:ring-wgreen/40 focus:border-wgreen/60 transition-colors';

/**
 * LoginPanel — lean email/password sign-in + registration (+ TOTP second
 * step, + Google when configured), embeddable anywhere (checkout step 1,
 * modals). Unlike LoginPage it does NOT navigate — it calls `onSuccess`
 * after the session is set, so the host decides what happens next.
 */
export default function LoginPanel({ onSuccess, defaultMode = 'login' }) {
  const [mode, setMode] = useState(defaultMode);
  const [form, setForm] = useState({ email: '', password: '', full_name: '' });
  const [pendingTotp, setPendingTotp] = useState(null);
  const [totpCode, setTotpCode] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const setSession = useAuthStore((s) => s.setSession);
  const setUser = useAuthStore((s) => s.setUser);

  const { data: authConfig } = useQuery({
    queryKey: ['auth-config'],
    queryFn: authApi.getConfig,
    staleTime: Infinity,
  });
  const googleEnabled = Boolean(authConfig?.google_login);

  const isRegister = mode === 'register';
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function finishLogin(tokens, emailHint) {
    setSession({
      user: { email: emailHint },
      accessToken: tokens.access_token,
      refreshToken: tokens.refresh_token,
    });
    try {
      setUser(await authApi.me());
    } catch {
      /* profile is non-critical */
    }
    onSuccess?.();
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
      } else {
        await finishLogin(resp, form.email);
      }
    } catch (err) {
      setError(
        err?.response?.data?.error?.message ||
          (isRegister ? 'Could not create the account.' : 'Invalid email or password.'),
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleTotp(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const tokens = await authApi.loginTotp(pendingTotp.token, totpCode.trim());
      await finishLogin(tokens, pendingTotp.email);
    } catch (err) {
      setError(err?.response?.data?.error?.message || 'Invalid code. Try again.');
    } finally {
      setBusy(false);
    }
  }

  /* ── TOTP second step ── */
  if (pendingTotp) {
    return (
      <form onSubmit={handleTotp} className="space-y-3">
        <p className="text-[13.5px] text-wmuted m-0">
          Enter the 6-digit code from your authenticator app.
        </p>
        <input
          value={totpCode}
          onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          placeholder="123456"
          inputMode="numeric"
          autoFocus
          className={inputCls}
          aria-label="Authentication code"
        />
        {error && <p className="text-[12.5px] text-red-600 m-0">{error}</p>}
        <button
          type="submit"
          disabled={busy || totpCode.length !== 6}
          className="w-full bg-wgreen text-white border-0 rounded-xl py-3.5 text-[14px] cursor-pointer hover:bg-wgreen-dark disabled:opacity-50 transition-colors"
        >
          {busy ? 'Verifying…' : 'Verify & Continue'}
        </button>
      </form>
    );
  }

  return (
    <div>
      {/* Mode tabs */}
      <div className="grid grid-cols-2 gap-1 rounded-xl bg-wpaper border border-wline p-1 mb-4">
        {[
          { key: 'login', label: 'Sign In' },
          { key: 'register', label: 'Create Account' },
        ].map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              setMode(key);
              setError(null);
            }}
            className={cn(
              'rounded-lg py-2 text-[13px] border-0 cursor-pointer transition-colors',
              mode === key ? 'bg-wgreen text-white' : 'bg-transparent text-wmuted hover:text-wink',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        {isRegister && (
          <input
            value={form.full_name}
            onChange={set('full_name')}
            placeholder="Full name"
            autoComplete="name"
            className={inputCls}
            aria-label="Full name"
          />
        )}
        <input
          type="email"
          value={form.email}
          onChange={set('email')}
          placeholder="Email address"
          autoComplete="email"
          required
          className={inputCls}
          aria-label="Email address"
        />
        <input
          type="password"
          value={form.password}
          onChange={set('password')}
          placeholder="Password"
          autoComplete={isRegister ? 'new-password' : 'current-password'}
          required
          minLength={8}
          className={inputCls}
          aria-label="Password"
        />
        {error && <p className="text-[12.5px] text-red-600 m-0">{error}</p>}
        <button
          type="submit"
          disabled={busy}
          className="w-full bg-wgreen text-white border-0 rounded-xl py-3.5 text-[14px] cursor-pointer hover:bg-wgreen-dark disabled:opacity-60 transition-colors"
        >
          {busy
            ? 'Please wait…'
            : isRegister
              ? 'Create Account & Continue'
              : 'Sign In & Continue'}
        </button>
      </form>

      {googleEnabled && (
        <a
          href={`${env.apiBaseUrl}/auth/google/login`}
          className="mt-3 flex items-center justify-center gap-2 rounded-xl border border-wline bg-wpaper py-3 text-[13.5px] text-wink no-underline hover:border-wgreen/50 transition-colors"
        >
          Continue with Google
        </a>
      )}
    </div>
  );
}
