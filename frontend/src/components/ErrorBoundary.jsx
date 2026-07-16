import { Component } from 'react';

/**
 * Root error boundary. Catches render-time crashes and shows a friendly retry
 * screen instead of a blank page.
 *
 * Chunk-load failures get special treatment: after a redeploy the hashed chunk
 * filenames change, so a long-lived tab that lazy-loads a route can hit a 404
 * for a chunk that no longer exists. We reload once to pull the fresh manifest.
 */

const CHUNK_ERROR_RE =
  /Loading chunk|Loading CSS chunk|dynamically imported module|Importing a module script failed|error loading dynamically imported module/i;

function isChunkLoadError(error) {
  if (!error) return false;
  if (error.name === 'ChunkLoadError') return true;
  return CHUNK_ERROR_RE.test(error.message || '');
}

// Guard against reload loops: only self-heal if we haven't just reloaded. If the
// chunk error survives a reload (comes back within the cooldown), we stop and
// show the fallback UI instead of looping forever.
const RELOAD_GUARD_KEY = 'wv:chunk-reload-at';
const RELOAD_COOLDOWN_MS = 10_000;

function reloadOnceForChunkError() {
  try {
    const last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY) || 0);
    if (Date.now() - last < RELOAD_COOLDOWN_MS) return false;
    sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
  } catch {
    // sessionStorage unavailable (private mode / blocked) — reload anyway.
  }
  window.location.reload();
  return true;
}

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, reloading: false };
    this.handleReset = this.handleReset.bind(this);
  }

  static getDerivedStateFromError(error) {
    return { error, reloading: isChunkLoadError(error) };
  }

  componentDidCatch(error, info) {
    if (isChunkLoadError(error)) {
      const reloading = reloadOnceForChunkError();
      // If the cooldown blocked the reload, drop the "updating" state so the
      // fallback renders instead of a perpetual spinner.
      if (!reloading) this.setState({ reloading: false });
      return;
    }
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.error('ErrorBoundary caught an error', error, info);
    }
  }

  handleReset() {
    this.setState({ error: null, reloading: false });
  }

  render() {
    const { error, reloading } = this.state;
    if (!error) return this.props.children;

    const chunk = isChunkLoadError(error);

    return (
      <div className="flex min-h-screen flex-col items-center justify-center bg-wcanvas px-6 py-16 text-center">
        <div className="w-full max-w-md animate-rise">
          <div
            className="mx-auto mb-5 grid size-16 place-items-center rounded-full bg-wgold/10 text-wgold"
            aria-hidden="true"
          >
            <svg
              width="30"
              height="30"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </div>

          <h1 className="mb-2 text-2xl font-semibold text-wink">
            {reloading ? 'Updating…' : 'Something went wrong'}
          </h1>
          <p className="mx-auto mb-8 max-w-sm text-sm text-wmuted">
            {reloading
              ? 'A new version is loading. One moment…'
              : chunk
                ? 'The app was updated. Please reload to get the latest version.'
                : 'An unexpected error occurred. You can try again or head back home.'}
          </p>

          {!reloading && (
            <div className="flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                onClick={chunk ? () => window.location.reload() : this.handleReset}
                className="rounded-full bg-wgreen px-8 py-3 text-sm font-semibold text-white transition-colors hover:bg-wgreen-dark"
              >
                {chunk ? 'Reload' : 'Try again'}
              </button>
              <a
                href="/"
                className="rounded-full border border-wline px-8 py-3 text-sm font-semibold text-wink transition-colors hover:border-wgreen/40 hover:text-wgreen"
              >
                Go home
              </a>
            </div>
          )}
        </div>
      </div>
    );
  }
}
