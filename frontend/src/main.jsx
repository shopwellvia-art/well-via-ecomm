import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import App from './app/App.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import Toaster from './components/ui/Toaster.jsx';
// Self-hosted Roboto (weights used by the Flipkart re-skin). Imported before
// global.css so the @font-face rules are registered first. Self-hosting keeps
// the font working under the strict production CSP (no Google Fonts exception).
import '@fontsource/roboto/400.css';
import '@fontsource/roboto/500.css';
import '@fontsource/roboto/700.css';
import '@fontsource/roboto/900.css';
// Self-hosted Wellvia storefront fonts (CSP-safe, no Google Fonts exception).
// Jost = body, EB Garamond = editorial headings (frontend-3 look), Cinzel = wordmark.
import '@fontsource/jost/300.css';
import '@fontsource/jost/400.css';
import '@fontsource/jost/500.css';
import '@fontsource/jost/600.css';
import '@fontsource/eb-garamond/400.css';
import '@fontsource/eb-garamond/400-italic.css';
import '@fontsource/eb-garamond/500.css';
import '@fontsource/eb-garamond/600.css';
import '@fontsource/eb-garamond/700.css';
import '@fontsource/cinzel/400.css';
import '@fontsource/cinzel/500.css';
import '@fontsource/cinzel/600.css';
import './styles/global.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      // Retry transient failures once, but never a 4xx (404, 401, …) —
      // those are deterministic, so retrying only delays the error state.
      retry: (failureCount, error) => {
        const status = error?.response?.status;
        if (status >= 400 && status < 500) return false;
        return failureCount < 1;
      },
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* Outermost so it also catches provider/router render crashes and self-heals
        chunk-load errors after a redeploy. */}
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
          {/* Global toast/announcer — mounted once, portals to <body>. */}
          <Toaster />
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>
);
