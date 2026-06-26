import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Copy, AlertTriangle } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { useAuthStore } from '@/features/auth/store.js';
import { authApi } from '@/features/auth/api.js';
import { totpApi } from '@/features/totp/api.js';
import AccountLayout from '@/components/storefront/AccountLayout';
import {
  ShieldIcon,
  UserIcon,
  LockIcon,
  Check,
  CloseIcon,
} from '@/components/storefront/Icons';

/* ── shared style constants ─────────────────────────────────────────────── */
const inputCls =
  'w-full bg-wpaper border border-wline rounded-xl px-[15px] py-[13px] text-[14px] text-wink ' +
  'placeholder:text-wmuted/60 focus:outline-none focus:border-wgreen transition-colors';

const labelCls = 'block text-[11px] tracking-[0.1em] uppercase text-wmuted mb-1.5';

const btnPrimary =
  'inline-flex items-center gap-2 rounded-full bg-wgreen border-0 px-7 py-3 text-[13.5px] ' +
  'text-white cursor-pointer hover:bg-wgreen-dark disabled:opacity-50 disabled:cursor-not-allowed transition-colors';

const btnOutline =
  'inline-flex items-center gap-2 rounded-full border border-wline bg-transparent ' +
  'px-5 py-2 text-[13px] text-wink cursor-pointer hover:border-wgreen transition-colors ' +
  'disabled:opacity-50 disabled:cursor-not-allowed';

const btnGhost =
  'bg-transparent border-0 text-wmuted text-[13px] cursor-pointer hover:text-wink ' +
  'transition-colors disabled:opacity-40 px-3 py-2';

/* ── useSystemConfig ────────────────────────────────────────────────────── */
function useSystemConfig() {
  return useQuery({
    queryKey: ['auth-config'],
    queryFn: authApi.getConfig,
    staleTime: Infinity,
  });
}

/* ── CopyableCode ───────────────────────────────────────────────────────── */
function CopyableCode({ value }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard.writeText(value).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => {},
    );
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? 'Copied' : 'Copy secret key'}
      className="inline-flex items-center gap-1.5 rounded-full border border-wline bg-wpaper px-3 py-1.5 font-mono text-xs text-wink transition-colors hover:border-wgreen"
    >
      {copied ? (
        <Check size={12} stroke="#183A2E" />
      ) : (
        <Copy size={12} className="text-wmuted" />
      )}
      {value}
    </button>
  );
}

/* ── BackupCodesPanel ───────────────────────────────────────────────────── */
function BackupCodesPanel({ codes, onAcknowledge }) {
  return (
    <div className="rounded-xl border border-wgold/30 bg-wgold/10 p-5">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-wgold/15 text-wgold">
          <AlertTriangle size={18} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[13.5px] font-semibold text-wink">Save these backup codes</h3>
          <p className="mt-1 text-xs leading-relaxed text-wmuted">
            Each code can be used once if you lose access to your authenticator.
            We&apos;ll never show them again — copy them somewhere safe now.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
            {codes.map((c) => (
              <code
                key={c}
                className="rounded-lg border border-wline bg-wcard px-2 py-1.5 text-center font-mono text-sm text-wink"
              >
                {c}
              </code>
            ))}
          </div>
          {/* stack vertically on mobile */}
          <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(codes.join('\n')).catch(() => {});
              }}
              className={btnOutline}
            >
              <Copy size={14} aria-hidden="true" /> Copy all
            </button>
            <button type="button" onClick={onAcknowledge} className={btnPrimary}>
              <Check size={14} /> I&apos;ve saved them
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── EnrollmentFlow ─────────────────────────────────────────────────────── */
function EnrollmentFlow({ onDone, onCancel }) {
  const qc = useQueryClient();
  const [stage, setStage] = useState('starting');
  const [start, setStart] = useState(null);
  const [code, setCode] = useState('');
  const [error, setError] = useState(null);
  const [backup, setBackup] = useState(null);

  const startMut = useMutation({ mutationFn: totpApi.start });
  const confirmMut = useMutation({ mutationFn: totpApi.confirm });

  useEffect(() => {
    startMut.mutate(undefined, {
      onSuccess: (d) => {
        setStart(d);
        setStage('scan');
      },
      onError: (err) => {
        setError(
          err?.response?.data?.error?.message ||
            'Could not start enrollment. Please try again.',
        );
        setStage('error');
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function confirm() {
    setError(null);
    if (!/^\d{6}$/.test(code.trim())) {
      setError('Enter the 6-digit code shown in your authenticator app.');
      return;
    }
    try {
      const resp = await confirmMut.mutateAsync(code.trim());
      setBackup(resp.backup_codes);
      setStage('confirmed');
      authApi.me().then((u) => useAuthStore.getState().setUser(u)).catch(() => {});
      qc.invalidateQueries({ queryKey: ['auth', 'me'] });
    } catch (err) {
      setError(
        err?.response?.data?.error?.message ||
          "That code didn't match. Try the next one your app shows.",
      );
    }
  }

  /* ── starting ── */
  if (stage === 'starting') {
    return (
      <div className="flex items-center gap-3 p-5">
        <span
          className="h-5 w-5 rounded-full border-2 border-wline border-t-wgreen animate-spin"
          aria-hidden="true"
        />
        <p className="text-[13.5px] text-wmuted">Generating your secret&hellip;</p>
      </div>
    );
  }

  /* ── error ── */
  if (stage === 'error') {
    return (
      <div className="p-5">
        <p className="text-[13.5px] text-red-600">{error}</p>
        <button type="button" onClick={onCancel} className={btnGhost + ' mt-3'}>
          Close
        </button>
      </div>
    );
  }

  /* ── confirmed ── */
  if (stage === 'confirmed') {
    return (
      <div className="flex flex-col gap-4 p-5">
        <div className="rounded-xl border border-wline bg-wpaper p-4">
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-full bg-wgreen/10 text-wgreen">
              <ShieldIcon size={20} />
            </span>
            <div>
              <p className="text-[14px] font-semibold text-wink">
                Two-factor authentication is on.
              </p>
              <p className="mt-0.5 text-xs text-wmuted">
                You&apos;ll need a 6-digit code from your authenticator on every sign-in.
              </p>
            </div>
          </div>
        </div>
        <BackupCodesPanel codes={backup || []} onAcknowledge={onDone} />
      </div>
    );
  }

  /* ── scan ── */
  return (
    <div className="p-5">
      <h3 className="text-[14.5px] font-semibold text-wink">
        Scan with your authenticator
      </h3>
      <p className="mt-1 text-xs leading-relaxed text-wmuted">
        Use Google Authenticator, 1Password, Authy, or any TOTP app. After scanning,
        enter the 6-digit code below to confirm.
      </p>

      <div className="mt-5 grid gap-5 sm:grid-cols-[144px_minmax(0,1fr)]">
        {/* QR code */}
        <figure className="grid place-items-center self-start rounded-xl border border-wline bg-white p-2.5">
          <QRCodeSVG value={start.otpauth_uri} size={128} includeMargin={false} />
          <figcaption className="sr-only">
            Scan this QR code with your authenticator app to link it to your account.
          </figcaption>
        </figure>

        <div className="min-w-0">
          <p className={labelCls}>Or enter this secret manually</p>
          <div className="mt-2">
            <CopyableCode value={start.secret} />
          </div>
          <div className="mt-4">
            <label className={labelCls} htmlFor="totp-code">
              6-digit code
            </label>
            <input
              id="totp-code"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="123456"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              required
              className={inputCls}
            />
            {error && <p className="mt-1.5 text-xs text-red-600">{error}</p>}
          </div>
        </div>
      </div>

      <div className="mt-5 flex justify-end gap-2.5">
        <button
          type="button"
          onClick={onCancel}
          disabled={confirmMut.isPending}
          className={btnGhost}
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={confirm}
          disabled={confirmMut.isPending}
          className={btnPrimary}
        >
          {confirmMut.isPending ? (
            <span className="h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin" />
          ) : (
            <ShieldIcon size={16} stroke="white" />
          )}
          Confirm &amp; enable
        </button>
      </div>
    </div>
  );
}

/* ── ProfileCard ────────────────────────────────────────────────────────── */
function ProfileCard() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const [fullName, setFullName] = useState(user?.full_name || '');
  const [phone, setPhone] = useState(user?.phone || '');
  const [status, setStatus] = useState(null); // 'saved' | 'error'
  const [errorMsg, setErrorMsg] = useState(null);

  useEffect(() => {
    setFullName(user?.full_name || '');
    setPhone(user?.phone || '');
  }, [user?.full_name, user?.phone]);

  const save = useMutation({
    mutationFn: () =>
      authApi.updateMe({
        full_name: fullName.trim() || null,
        phone: phone.trim() || null,
      }),
    onSuccess: (updated) => {
      setUser(updated);
      setStatus('saved');
      setErrorMsg(null);
      setTimeout(() => setStatus(null), 2500);
    },
    onError: (err) => {
      setStatus('error');
      setErrorMsg(
        err?.response?.data?.error?.message ||
          err?.response?.data?.detail ||
          'Could not save your profile. Please try again.',
      );
    },
  });

  const dirty =
    (fullName || '') !== (user?.full_name || '') ||
    (phone || '') !== (user?.phone || '');

  return (
    <div className="bg-wcard border border-wline rounded-xl2 p-6 mb-[18px]">
      {/* Card heading */}
      <div className="flex items-center gap-2 mb-[18px]">
        <UserIcon size={18} stroke="#6F6A60" />
        <div className="font-wserif text-[20px] text-wink">Profile</div>
      </div>

      <p className="text-xs leading-relaxed text-wmuted mb-4">
        Your email{' '}
        <span className="font-medium text-wink">{user.email}</span>{' '}
        can&apos;t be changed here. Add a phone number to opt in to SMS updates on
        order paid / shipped events.
      </p>

      <div className="grid gap-3.5 sm:grid-cols-2">
        <div>
          <label className={labelCls} htmlFor="profile-name">
            Full Name
          </label>
          <input
            id="profile-name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Jane Doe"
            autoComplete="name"
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls} htmlFor="profile-phone">
            Phone (for SMS)
          </label>
          <input
            id="profile-phone"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+14155551234"
            inputMode="tel"
            autoComplete="tel"
            className={inputCls}
          />
          <p className="mt-1 text-[11px] text-wmuted">
            Include country code. Leave blank to opt out of SMS.
          </p>
        </div>
      </div>

      {/* Feedback */}
      {status === 'saved' && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-wgreen">
          <Check size={14} stroke="#183A2E" /> Saved.
        </p>
      )}
      {status === 'error' && (
        <p className="mt-3 flex items-center gap-1.5 text-xs text-red-600">
          <AlertTriangle size={14} aria-hidden="true" /> {errorMsg}
        </p>
      )}

      <div className="mt-5 flex justify-end">
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className={btnPrimary}
        >
          {save.isPending && (
            <span className="h-4 w-4 rounded-full border-2 border-white/30 border-t-white animate-spin" />
          )}
          Save Changes
        </button>
      </div>
    </div>
  );
}

/* ── AccountSecurityPage ────────────────────────────────────────────────── */
export default function AccountSecurityPage() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);

  // show skeleton while system config loads — prevents "Unavailable" badge flash
  const { data: cfg, isLoading: cfgLoading } = useSystemConfig();
  const [enrolling, setEnrolling] = useState(false);

  // stateful inline confirm instead of window.confirm
  const [disableConfirming, setDisableConfirming] = useState(false);
  // surface disable mutation errors
  const [disableError, setDisableError] = useState(null);

  const disable = useMutation({
    mutationFn: () => totpApi.disable(),
    onSuccess: () => {
      setDisableConfirming(false);
      setDisableError(null);
      authApi.me().then((u) => setUser(u)).catch(() => {});
    },
    onError: (err) => {
      setDisableConfirming(false);
      setDisableError(
        err?.response?.data?.error?.message ||
          err?.response?.data?.detail ||
          'Could not disable two-factor authentication. Please try again.',
      );
    },
  });

  /* ── not signed in ── */
  if (!user) {
    return (
      <AccountLayout active="security">
        <div className="flex flex-col items-center gap-5 py-20 text-center">
          <span className="grid size-14 place-items-center rounded-full bg-wline/60">
            <LockIcon size={26} stroke="#6F6A60" />
          </span>
          <p className="text-[15px] text-wmuted">Sign in to manage your account security.</p>
          <Link
            to="/login?next=/account/security"
            className="inline-block rounded-full bg-wgreen px-7 py-3 text-[13.5px] text-white no-underline hover:bg-wgreen-dark transition-colors"
          >
            Sign in
          </Link>
        </div>
      </AccountLayout>
    );
  }

  const systemEnabled = !!cfg?.totp_enabled_system_wide;
  const totpOn = !!user.totp_enabled;

  return (
    <AccountLayout active="security">
      {/* Page header */}
      <h1 className="font-wserif font-medium text-[clamp(26px,3.4vw,38px)] m-0 mb-1.5 text-wink">
        Account &amp; Security
      </h1>
      <p className="text-[14px] text-wmuted font-light m-0 mb-6">
        Manage your personal details and how you sign in.
      </p>

      <div className="max-w-2xl">
        {/* ── Profile ── */}
        <ProfileCard />

        {/* ── Security / 2FA ── */}

        {cfgLoading ? (
          /* Loading skeleton */
          <div className="h-24 rounded-xl2 bg-wline/50 animate-pulse" />
        ) : !systemEnabled && !totpOn ? (
          /* System has 2FA disabled */
          <div className="bg-wcard border border-wline rounded-xl2 p-6">
            <div className="flex items-center gap-2 mb-[18px]">
              <ShieldIcon size={18} stroke="#6F6A60" />
              <div className="font-wserif text-[20px] text-wink">Security</div>
              <span className="ml-1 rounded-full border border-wline px-2.5 py-0.5 text-[11px] text-wmuted">
                Unavailable
              </span>
            </div>
            <p className="text-[13.5px] leading-relaxed text-wmuted">
              Your administrator hasn&apos;t enabled two-factor authentication yet. If you need
              it, reach out to support.
            </p>
          </div>
        ) : enrolling ? (
          /* Enrollment wizard */
          <div
            aria-live="polite"
            className="bg-wcard border border-wline rounded-xl2 overflow-hidden"
          >
            <div className="flex items-center justify-between border-b border-wline px-6 py-4">
              <div className="flex items-center gap-2">
                <ShieldIcon size={18} stroke="#183A2E" />
                <div className="font-wserif text-[20px] text-wink">Set up authenticator</div>
              </div>
              <button
                type="button"
                onClick={() => setEnrolling(false)}
                aria-label="Cancel enrollment"
                className="grid size-8 place-items-center rounded-full border border-wline bg-transparent cursor-pointer hover:border-wgreen transition-colors"
              >
                <CloseIcon size={16} stroke="#6F6A60" />
              </button>
            </div>
            <EnrollmentFlow
              onCancel={() => setEnrolling(false)}
              onDone={() => setEnrolling(false)}
            />
          </div>
        ) : totpOn ? (
          /* 2FA is active — show toggle + disable control */
          <div className="bg-wcard border border-wline rounded-xl2 p-6">
            <div className="flex items-center gap-2 mb-[18px]">
              <ShieldIcon size={18} stroke="#183A2E" />
              <div className="font-wserif text-[20px] text-wink">Security</div>
            </div>

            {/* 2FA active row */}
            <div className="flex flex-wrap gap-3.5 justify-between items-center py-4 border-t border-wline">
              <div>
                <div className="text-[14.5px] text-wink">Two-Factor Authentication</div>
                <div className="text-[12.5px] text-wmuted">Your account is protected</div>
              </div>
              {/* Toggle visual — on state */}
              <div className="flex items-center gap-2 text-[12px] text-wgreen">
                <span className="w-[38px] h-[22px] rounded-full bg-wgreen relative inline-block shrink-0">
                  <span className="absolute top-0.5 right-0.5 w-[18px] h-[18px] rounded-full bg-white" />
                </span>
                On
              </div>
            </div>

            {/* Inline disable confirm */}
            <div className="mt-4 flex flex-col items-end gap-2">
              {disableConfirming ? (
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setDisableConfirming(false);
                      setDisableError(null);
                    }}
                    disabled={disable.isPending}
                    className={btnGhost}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => disable.mutate()}
                    disabled={disable.isPending}
                    className="inline-flex items-center gap-2 rounded-full border border-wline bg-transparent px-5 py-2 text-[13px] text-wink cursor-pointer hover:border-red-400 hover:text-red-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {disable.isPending ? (
                      <span className="h-4 w-4 rounded-full border-2 border-wline border-t-wgreen animate-spin" />
                    ) : (
                      <CloseIcon size={14} />
                    )}
                    Confirm disable
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setDisableError(null);
                    setDisableConfirming(true);
                  }}
                  disabled={disable.isPending}
                  className="inline-flex items-center gap-2 rounded-full border border-wline bg-transparent px-5 py-2 text-[13px] text-wink cursor-pointer hover:border-red-400 hover:text-red-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <CloseIcon size={14} /> Disable
                </button>
              )}
              {disableError && (
                <p className="text-xs text-red-600">{disableError}</p>
              )}
            </div>
          </div>
        ) : (
          /* Offer enrollment */
          <div className="bg-wcard border border-wline rounded-xl2 p-6">
            <div className="flex items-center gap-2 mb-[18px]">
              <ShieldIcon size={18} stroke="#6F6A60" />
              <div className="font-wserif text-[20px] text-wink">Security</div>
              <span className="ml-1 rounded-full border border-wgold text-wgold px-2.5 py-0.5 text-[11px]">
                Recommended
              </span>
            </div>

            {/* 2FA off row with toggle visual */}
            <div className="flex flex-wrap gap-3.5 justify-between items-center py-4 border-t border-wline">
              <div>
                <div className="text-[14.5px] text-wink">Two-Factor Authentication</div>
                <div className="text-[12.5px] text-wmuted">Add an extra layer of security</div>
              </div>
              {/* Toggle visual — off state */}
              <span className="w-[38px] h-[22px] rounded-full bg-wline relative inline-block shrink-0">
                <span className="absolute top-0.5 left-0.5 w-[18px] h-[18px] rounded-full bg-white" />
              </span>
            </div>

            <p className="text-[13.5px] leading-relaxed text-wmuted mt-1 mb-5">
              Pair your account with an authenticator app (Google Authenticator, 1Password,
              Authy, etc.). Even if your password leaks, attackers won&apos;t get in without
              the 6-digit code that rotates every 30 seconds.
            </p>
            <button
              type="button"
              onClick={() => setEnrolling(true)}
              className={btnPrimary}
            >
              <ShieldIcon size={16} stroke="white" /> Set up two-factor authentication
            </button>
          </div>
        )}
      </div>
    </AccountLayout>
  );
}
