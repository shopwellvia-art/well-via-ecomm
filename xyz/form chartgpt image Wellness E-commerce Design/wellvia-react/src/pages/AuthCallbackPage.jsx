import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';

/**
 * AuthCallbackPage — landing target for OAuth / magic-link returns.
 * Shows a verifying spinner, then redirect to the account area.
 * Wire the real token exchange in the useEffect below.
 */
export default function AuthCallbackPage() {
  const navigate = useNavigate();

  useEffect(() => {
    // TODO: exchange the auth code / verify the session here, then:
    const t = setTimeout(() => navigate('/account'), 2200);
    return () => clearTimeout(t);
  }, [navigate]);

  return (
    <main className="paper min-h-screen flex items-start justify-center pt-[clamp(48px,12vh,140px)] px-6 text-center">
      <div className="animate-rise">
        <div className="w-[46px] h-[46px] border-[3px] border-line border-t-green rounded-full mx-auto mb-6" style={{ animation: 'spin360 1s linear infinite' }} />
        <h1 className="font-serif font-medium text-[clamp(26px,3vw,34px)] m-0 mb-2">Signing you in…</h1>
        <p className="text-[14px] text-muted font-light m-0">Verifying your secure session. This only takes a moment.</p>
      </div>
    </main>
  );
}
