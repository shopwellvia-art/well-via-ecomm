import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Loader2, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/Button.jsx';
import { authApi } from '@/features/auth/api.js';
import { useAuthStore } from '@/features/auth/store.js';
import { heroContainer, fadeUp } from '@/lib/motion.js';

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
    (async () => {
      setSession({ user: null, accessToken, refreshToken });
      let profile = null;
      try {
        profile = await authApi.me();
        setUser(profile);
      } catch {
        /* profile is non-critical for storefront use */
      }
      // Strip tokens from the visible URL.
      window.history.replaceState(null, '', '/auth/callback');
      if (!cancelled) navigate(profile?.is_admin ? '/admin' : '/', { replace: true });
    })();

    return () => {
      cancelled = true;
    };
  }, [navigate, setSession, setUser]);

  return (
    <main className="relative mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-content items-center justify-center px-6 py-12">
      {/* Ambient glow */}
      <div
        aria-hidden="true"
        className="absolute left-1/2 top-1/3 -z-10 size-[400px] -translate-x-1/2 rounded-full bg-accent/10 blur-[140px]"
      />

      {errorMsg ? (
        <motion.div
          variants={heroContainer}
          initial="hidden"
          animate="show"
          className="flex max-w-sm flex-col items-center text-center"
        >
          <motion.span
            variants={fadeUp}
            className="grid size-14 place-items-center rounded-full bg-danger/12 text-danger shadow-glow-danger"
          >
            <AlertTriangle className="size-6" aria-hidden="true" />
          </motion.span>
          <motion.h1 variants={fadeUp} className="mt-5 text-h2 tracking-tight text-ink-primary">
            Sign-in failed
          </motion.h1>
          <motion.p variants={fadeUp} className="mt-1.5 text-sm text-ink-secondary">
            {errorMsg}
          </motion.p>
          <motion.div variants={fadeUp} className="mt-6">
            <Link to="/login">
              <Button size="lg">Back to sign in</Button>
            </Link>
          </motion.div>
        </motion.div>
      ) : (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="flex flex-col items-center gap-4 text-ink-secondary"
        >
          <span className="grid size-12 place-items-center rounded-full bg-accent/10">
            <Loader2 className="size-6 animate-spin text-accent" aria-hidden="true" />
          </span>
          <p className="text-sm font-medium text-ink-secondary">Signing you in…</p>
        </motion.div>
      )}
    </main>
  );
}
