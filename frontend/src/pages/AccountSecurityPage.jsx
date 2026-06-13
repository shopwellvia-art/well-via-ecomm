import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Shield,
  ShieldCheck,
  Lock,
  Copy,
  Check,
  AlertTriangle,
  X,
  User as UserIcon,
  Phone,
} from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { Page } from '@/components/layout/Page.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { useAuthStore } from '@/features/auth/store.js';
import { authApi } from '@/features/auth/api.js';
import { totpApi } from '@/features/totp/api.js';

function useSystemConfig() {
  return useQuery({
    queryKey: ['auth-config'],
    queryFn: authApi.getConfig,
    staleTime: Infinity,
  });
}

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
      className="inline-flex items-center gap-1.5 rounded-xs border border-line-subtle bg-bg-sunken px-2.5 py-1.5 font-mono text-xs text-ink-primary transition-colors hover:border-line-strong hover:bg-bg-elevated focus-visible:focus-ring"
    >
      {copied ? (
        <Check className="size-3 text-success" aria-hidden="true" />
      ) : (
        <Copy className="size-3 text-ink-tertiary" aria-hidden="true" />
      )}
      {value}
    </button>
  );
}

function BackupCodesPanel({ codes, onAcknowledge }) {
  return (
    <div className="rounded-sm border border-warning/30 bg-warning/8 p-4">
      <div className="flex items-start gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-full bg-warning/15 text-warning">
          <AlertTriangle className="size-5" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-ink-primary">
            Save these backup codes
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
            Each code can be used once if you lose access to your authenticator.
            We&apos;ll never show them again — copy them somewhere safe now.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-5">
            {codes.map((c) => (
              <code
                key={c}
                className="nums rounded-xs border border-line-subtle bg-bg-elevated px-2 py-1.5 text-center font-mono text-sm text-ink-primary"
              >
                {c}
              </code>
            ))}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                navigator.clipboard.writeText(codes.join('\n')).catch(() => {});
              }}
            >
              <Copy className="size-4" aria-hidden="true" /> Copy all
            </Button>
            <Button size="sm" onClick={onAcknowledge}>
              <Check className="size-4" aria-hidden="true" /> I&apos;ve saved them
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

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

  if (stage === 'starting') {
    return (
      <div className="flex items-center gap-3 p-4">
        <span className="size-5 animate-spin rounded-full border-2 border-line-subtle border-t-accent" aria-hidden="true" />
        <p className="text-sm text-ink-secondary">Generating your secret&hellip;</p>
      </div>
    );
  }
  if (stage === 'error') {
    return (
      <div className="p-4">
        <p className="text-sm text-danger">{error}</p>
        <Button variant="ghost" className="mt-3" onClick={onCancel}>
          Close
        </Button>
      </div>
    );
  }
  if (stage === 'confirmed') {
    return (
      <div className="flex flex-col gap-4">
        <div className="rounded-sm border border-line-subtle bg-bg-elevated p-4">
          <div className="flex items-center gap-3">
            <span className="grid size-9 place-items-center rounded-full bg-success/10 text-success">
              <ShieldCheck className="size-5" aria-hidden="true" />
            </span>
            <div>
              <p className="text-sm font-semibold text-ink-primary">
                Two-factor authentication is on.
              </p>
              <p className="mt-0.5 text-xs text-ink-secondary">
                You&apos;ll need a 6-digit code from your authenticator on every sign-in.
              </p>
            </div>
          </div>
        </div>
        <BackupCodesPanel codes={backup || []} onAcknowledge={onDone} />
      </div>
    );
  }

  // stage === 'scan'
  return (
    <div className="p-4">
      <h3 className="text-sm font-semibold text-ink-primary">
        Scan with your authenticator
      </h3>
      <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
        Use Google Authenticator, 1Password, Authy, or any TOTP app. After scanning,
        enter the 6-digit code below to confirm.
      </p>

      <div className="mt-5 grid gap-5 sm:grid-cols-[144px_minmax(0,1fr)]">
        {/* QR code */}
        <div className="grid place-items-center self-start rounded-sm border border-line-subtle bg-white p-2.5 shadow-sm">
          <QRCodeSVG value={start.otpauth_uri} size={128} includeMargin={false} />
        </div>
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-tertiary">
            Or enter this secret manually
          </p>
          <div className="mt-2">
            <CopyableCode value={start.secret} />
          </div>
          <div className="mt-4">
            <Input
              label="6-digit code"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="123456"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              error={error}
              required
            />
          </div>
        </div>
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={confirmMut.isPending}>
          Cancel
        </Button>
        <Button onClick={confirm} loading={confirmMut.isPending}>
          <ShieldCheck className="size-4" aria-hidden="true" /> Confirm &amp; enable
        </Button>
      </div>
    </div>
  );
}

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
    <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
      <div className="border-b border-line-subtle bg-bg-sunken px-4 py-3">
        <div className="flex items-center gap-2">
          <UserIcon className="size-4 text-ink-tertiary" aria-hidden="true" />
          <h2 className="text-sm font-semibold text-ink-primary">Personal information</h2>
        </div>
      </div>
      <div className="p-4">
        <p className="text-xs leading-relaxed text-ink-secondary">
          Your email{' '}
          <span className="font-medium text-ink-primary">{user.email}</span>{' '}
          can&apos;t be changed here. Add a phone number to opt in to SMS updates on
          order paid / shipped events.
        </p>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Input
            label="Full name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Jane Doe"
            autoComplete="name"
          />
          <Input
            label="Phone (for SMS notifications)"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+14155551234"
            inputMode="tel"
            autoComplete="tel"
            helper="Include country code. Leave blank to opt out of SMS."
          />
        </div>

        {status === 'saved' && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-success">
            <Check className="size-3.5" aria-hidden="true" /> Saved.
          </p>
        )}
        {status === 'error' && (
          <p className="mt-3 flex items-center gap-1.5 text-xs text-danger">
            <AlertTriangle className="size-3.5" aria-hidden="true" /> {errorMsg}
          </p>
        )}

        <div className="mt-4 flex justify-end">
          <Button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            loading={save.isPending}
          >
            <Phone className="size-4" aria-hidden="true" /> Save
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function AccountSecurityPage() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const { data: cfg } = useSystemConfig();
  const [enrolling, setEnrolling] = useState(false);

  const disable = useMutation({
    mutationFn: () => totpApi.disable(),
    onSuccess: () => {
      authApi.me().then((u) => setUser(u)).catch(() => {});
    },
  });

  if (!user) {
    return (
      <Page>
        <h1 className="text-lg font-semibold text-ink-primary">Account security</h1>
        <div className="mt-6">
          <EmptyState
            icon={Lock}
            title="Sign in first"
            action={
              <Link to="/login?next=/account/security">
                <Button size="sm">Sign in</Button>
              </Link>
            }
          />
        </div>
      </Page>
    );
  }

  const systemEnabled = !!cfg?.totp_enabled_system_wide;
  const totpOn = !!user.totp_enabled;

  return (
    <Page>
      {/* Page header */}
      <div className="mb-5 flex items-center gap-3 border-b border-line-subtle pb-4">
        <span className="grid size-9 shrink-0 place-items-center rounded-sm bg-accent/10 text-accent">
          <Shield className="size-5" aria-hidden="true" />
        </span>
        <div>
          <h1 className="text-lg font-semibold text-ink-primary">Account security</h1>
          <p className="text-xs text-ink-secondary">
            Manage your profile and two-factor authentication.
          </p>
        </div>
      </div>

      <div className="max-w-2xl space-y-4">
        {/* Profile section */}
        <ProfileCard />

        {/* 2FA section */}
        {!systemEnabled && !totpOn ? (
          /* System disabled */
          <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
            <div className="border-b border-line-subtle bg-bg-sunken px-4 py-3">
              <div className="flex items-center gap-2">
                <Shield className="size-4 text-ink-tertiary" aria-hidden="true" />
                <h2 className="text-sm font-semibold text-ink-primary">
                  Two-factor authentication
                </h2>
                <Badge tone="neutral">Unavailable</Badge>
              </div>
            </div>
            <div className="p-4">
              <p className="text-sm leading-relaxed text-ink-secondary">
                Your administrator hasn&apos;t enabled this feature yet. If you need it,
                reach out to support.
              </p>
            </div>
          </div>
        ) : enrolling ? (
          <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
            <div className="flex items-center justify-between border-b border-line-subtle bg-bg-sunken px-4 py-3">
              <div className="flex items-center gap-2">
                <Shield className="size-4 text-accent" aria-hidden="true" />
                <h2 className="text-sm font-semibold text-ink-primary">
                  Set up authenticator
                </h2>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setEnrolling(false)}>
                <X className="size-4" aria-hidden="true" /> Cancel
              </Button>
            </div>
            <EnrollmentFlow
              onCancel={() => setEnrolling(false)}
              onDone={() => setEnrolling(false)}
            />
          </div>
        ) : totpOn ? (
          /* 2FA active */
          <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
            <div className="border-b border-line-subtle bg-bg-sunken px-4 py-3">
              <div className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-success" aria-hidden="true" />
                <h2 className="text-sm font-semibold text-ink-primary">
                  Two-factor authentication
                </h2>
                <Badge tone="success" dot>Active</Badge>
              </div>
            </div>
            <div className="flex items-start justify-between gap-3 p-4">
              <p className="text-xs leading-relaxed text-ink-secondary">
                You&apos;ll be asked for a 6-digit code from your authenticator every time
                you sign in.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={() => {
                  if (
                    window.confirm(
                      'Turn off two-factor authentication? Your account will be protected by password only.',
                    )
                  ) {
                    disable.mutate();
                  }
                }}
                loading={disable.isPending}
              >
                <X className="size-4" aria-hidden="true" /> Disable
              </Button>
            </div>
          </div>
        ) : (
          /* Offer enrollment */
          <div className="overflow-hidden rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
            <div className="border-b border-line-subtle bg-bg-sunken px-4 py-3">
              <div className="flex items-center gap-2">
                <Shield className="size-4 text-ink-tertiary" aria-hidden="true" />
                <h2 className="text-sm font-semibold text-ink-primary">
                  Two-factor authentication
                </h2>
                <Badge tone="accent" outline>Recommended</Badge>
              </div>
            </div>
            <div className="p-4">
              <p className="text-sm leading-relaxed text-ink-secondary">
                Pair your account with an authenticator app (Google Authenticator,
                1Password, Authy, etc.). Even if your password leaks, attackers won&apos;t
                get in without the 6-digit code that rotates every 30 seconds.
              </p>
              <Button className="mt-4" onClick={() => setEnrolling(true)}>
                <Shield className="size-4" aria-hidden="true" /> Set up two-factor authentication
              </Button>
            </div>
          </div>
        )}
      </div>
    </Page>
  );
}
