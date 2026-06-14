import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Loader2, AlertTriangle } from 'lucide-react';
import { buttonVariants } from '@/components/ui/Button.jsx';
import { cn } from '@/lib/utils.js';
import { authApi } from '@/features/auth/api.js';
import { useAuthStore } from '@/features/auth/store.js';

const ERROR_MESSAGES = {
  state: 'Security check failed. Please try signing in again.',
  google: "Google sign-in didn't complete. Please try again.",
  google_disabled: 'Google sign-in is not enabled for this store.',
  disabled: 'Your account has been disabled.',
};

/**
 * Lands here after the backend's Google OAuth callback. The backend appends
 * tokens (or an error) to the URL fragment; this page consumes them.
 */
export default function AuthCallbackPage() {
  const navigate = useNavigate();
  const setSession = useAuthStore((s) => s.setSession);
  const setUser = useAuthStore((s) => s.setUser);
  const [errorMsg, setErrorMsg] = useState(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const error = params.get('error');
    const accessToken = params.get('access_token');
    const refreshToken = params.get('refresh_token');

    if (error || !accessToken) {
      setErrorMsg(ERROR_MESSAGES[error] || 'Sign-in failed. Please try again.');
      return;
    }

    let cancelled = false;
    const TIMEOUT_MS = 10_000;
    (async () => {
      setSession({ user: null, accessToken, refreshToken });
      let profile = null;
      try {
        const timeout = new Promise((_, reject) =>
          setTimeout(() => reject(new Error('timeout')), TIMEOUT_MS),
        );
        profile = await Promise.race([authApi.me().then((p) => p), timeout]);
        setUser(profile);
      } catch (err) {
        if (err?.message === 'timeout') {
          setErrorMsg('Sign-in timed out. Please try again.');
          return;
        }
        /* profile is non-critical for storefront use */
      }
      if (!cancelled) {
        window.history.replaceState(null, '', '/auth/callback');
        navigate(profile?.is_admin ? '/admin' : '/', { replace: true });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [navigate, setSession, setUser]);

  return (
    <main className="flex min-h-[calc(100vh-4rem)] w-full items-center justify-center bg-bg-base px-4">
      <div className="w-full max-w-sm" aria-live="polite" aria-atomic="true">
        {errorMsg ? (
          /* Error card */
          <div className="rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
            <div className="rounded-t-sm bg-danger px-6 py-4">
              <h1 className="text-base font-semibold text-white">Sign-in failed</h1>
            </div>
            <div className="flex flex-col items-center px-6 py-8 text-center">
              <span className="grid size-14 place-items-center rounded-full bg-danger/12 text-danger">
                <AlertTriangle className="size-6" aria-hidden="true" />
              </span>
              <p className="mt-4 text-sm text-ink-secondary">{errorMsg}</p>
              <Link
                to="/login"
                className={cn(buttonVariants({ size: 'lg', block: true }), 'mt-6')}
              >
                Back to login
              </Link>
            </div>
          </div>
        ) : (
          /* Loading card */
          <div className="rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
            <div className="rounded-t-sm bg-accent px-6 py-4">
              <h1 className="text-base font-semibold text-white">Signing you in</h1>
            </div>
            <div className="flex flex-col items-center px-6 py-8 text-center">
              <span className="grid size-12 place-items-center rounded-full bg-accent/12">
                <Loader2 className="size-6 animate-spin text-accent" aria-hidden="true" />
              </span>
              <p className="mt-4 text-sm text-ink-secondary">
                Please wait while we complete your sign-in&hellip;
              </p>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
