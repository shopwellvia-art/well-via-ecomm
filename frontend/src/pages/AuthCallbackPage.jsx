import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { authApi } from '@/features/auth/api.js';
import { useAuthStore } from '@/features/auth/store.js';
import Logo from '@/components/storefront/Logo.jsx';
import { CloseIcon } from '@/components/storefront/Icons.jsx';

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
    <main className="paper bg-wcanvas min-h-screen flex items-start justify-center pt-[clamp(48px,12vh,140px)] px-6 text-center">
      <div className="w-full max-w-xs animate-rise" aria-live="polite" aria-atomic="true">

        {/* Brand mark */}
        <div className="flex justify-center mb-10">
          <Logo size="lg" stacked to="/" />
        </div>

        {errorMsg ? (
          /* ---- Error state ---- */
          <div className="flex flex-col items-center">
            <span className="grid size-14 place-items-center rounded-full bg-wgreen/10 mb-5">
              <CloseIcon size={22} stroke="#183A2E" strokeWidth={2} />
            </span>

            <h1 className="font-wserif font-medium text-[clamp(22px,2.8vw,28px)] text-wink mb-2">
              Sign-in failed
            </h1>

            <p className="text-[14px] text-wmuted font-light leading-relaxed mb-8">
              {errorMsg}
            </p>

            <Link
              to="/login"
              className="inline-block w-full rounded-full bg-wgreen py-3 text-center text-sm font-medium tracking-wide text-white hover:bg-wgreen-dark transition-colors"
            >
              Back to login
            </Link>
          </div>
        ) : (
          /* ---- Loading state ---- */
          <div className="flex flex-col items-center">
            <div
              className="w-[46px] h-[46px] border-[3px] border-wline border-t-wgreen rounded-full mx-auto mb-6 animate-spin360"
            />

            <h1 className="font-wserif font-medium text-[clamp(26px,3vw,34px)] text-wink mb-2">
              Signing you in…
            </h1>

            <p className="text-[14px] text-wmuted font-light">
              Verifying your secure session. This only takes a moment.
            </p>
          </div>
        )}

      </div>
    </main>
  );
}
