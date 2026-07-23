/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // ── Wellvia wellness palette (storefront re-skin) ─────────────────
        // Additive, w-prefixed so they never collide with the Flipkart tokens
        // below (which the admin UI depends on). Ported reference classes map
        // bg-page→bg-wcanvas, bg-bg→bg-wpaper, bg-card→bg-wcard, green→wgreen,
        // greenh→wgreen-dark, ink→wink, muted→wmuted, line→wline, gold→wgold.
        wcanvas: '#F7F5F0', // cream page canvas (floral bg sits on this)
        wpaper: '#FBFAF8', // near-white paper background
        wcard: '#FFFFFF', // cards / surfaces (pure white, frontend-3)
        // Three greens from frontend-3: DEFAULT (#044D39) for buttons/links,
        // dark (#0B3D2C) for hover/deep, deep (#092F24) for header/footer bands,
        // mid (#2E7D5B) for inline accents.
        wgreen: { DEFAULT: '#044D39', dark: '#0B3D2C', deep: '#092F24', mid: '#2E7D5B' },
        wink: '#141D18', // main / heading text (ink-strong)
        wmuted: '#5C6A62', // secondary text
        wline: '#E7E7E2', // borders / hairlines
        wgold: '#E0A82E', // gold accent + rating stars
        wsage: '#CDE3C3', // sage — savings strips / "you save" pills

        // Surface + ink are theme-driven — see :root/.light blocks in global.css.
        // RGB-triplet vars keep Tailwind opacity modifiers (e.g. bg-bg-base/50) working.
        bg: {
          base: 'rgb(var(--bg-base) / <alpha-value>)',
          elevated: 'rgb(var(--bg-elevated) / <alpha-value>)',
          sunken: 'rgb(var(--bg-sunken) / <alpha-value>)',
        },
        ink: {
          primary: 'rgb(var(--ink-primary) / <alpha-value>)',
          secondary: 'rgb(var(--ink-secondary) / <alpha-value>)',
          tertiary: 'rgb(var(--ink-tertiary) / <alpha-value>)',
          inverse: 'rgb(var(--ink-inverse) / <alpha-value>)',
        },
        // Alpha-based surfaces — referenced directly (no opacity modifier).
        line: {
          subtle: 'var(--line-subtle)',
          strong: 'var(--line-strong)',
        },
        fill: {
          DEFAULT: 'var(--fill)',
          strong: 'var(--fill-strong)',
        },
        glass: 'var(--glass)',
        // Brand + status colors. Flipkart marketplace palette.
        // accent = Flipkart blue — header, links, prices, selected states.
        accent: {
          DEFAULT: '#2874F0',
          hover: '#1F63D6',
          press: '#1A55BA',
          // Mid-ramp of the gradient — useful as a standalone tint
          mid: '#5C97F5',
          // Light ramp — for tinted backgrounds in tonal badges
          soft: 'rgba(40, 116, 240, 0.10)',
          // shadcn/ui token — used by the ghost/outline variants in button-base.jsx.
          foreground: 'hsl(var(--accent-foreground))',
        },
        // CTA orange — "Buy Now" / "Place Order" primary action.
        cta: {
          DEFAULT: '#FB641E',
          hover: '#E85610',
          press: '#D44E0A',
          soft: 'rgba(251, 100, 30, 0.10)',
        },
        // CTA yellow/amber — "Add to Cart".
        cart: {
          DEFAULT: '#FF9F00',
          hover: '#F59300',
          press: '#E08800',
          soft: 'rgba(255, 159, 0, 0.12)',
        },
        // Rating / discount green — rating pills and "X% off" text.
        rating: {
          DEFAULT: '#388E3C',
          soft: 'rgba(56, 142, 60, 0.12)',
        },
        success: {
          DEFAULT: '#388E3C',
          soft: 'rgba(56, 142, 60, 0.12)',
        },
        warning: {
          DEFAULT: '#F59E0B',
          soft: 'rgba(245, 158, 11, 0.12)',
        },
        danger: {
          DEFAULT: '#EF4444',
          soft: 'rgba(239, 68, 68, 0.12)',
        },
        // Info tone — used by KPI cards and status badges
        info: {
          DEFAULT: '#38BDF8',
          soft: 'rgba(56, 189, 248, 0.12)',
        },

        // ── shadcn/ui tokens ──────────────────────────────────────────────
        // Additive layer consumed by src/components/ui/button-base.jsx (and any
        // future shadcn primitives). Values resolve from the HSL CSS variables
        // defined in src/styles/global.css. None of these names are used by the
        // project's own design tokens above, so nothing here overrides them.
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
      },
      fontFamily: {
        sans: ['Roboto', 'system-ui', '-apple-system', 'Segoe UI', 'Arial', 'sans-serif'],
        // Wellvia storefront type — self-hosted via @fontsource (CSP-safe).
        display: ['Cinzel', 'serif'], // wordmark / display headings
        wserif: ['"EB Garamond"', 'Georgia', 'serif'], // editorial headings (frontend-3)
        wsans: ['Jost', 'system-ui', 'sans-serif'], // storefront body
        cormorant: ['Cormorant Garamond', 'serif'],
      },
      fontSize: {
        display: ['clamp(2.5rem, 6vw, 3.5rem)', { lineHeight: '1.07', letterSpacing: '-0.02em', fontWeight: '600' }],
        h1: ['clamp(2rem, 4vw, 2.5rem)', { lineHeight: '1.15', letterSpacing: '-0.015em', fontWeight: '600' }],
        h2: ['1.875rem', { lineHeight: '1.27', fontWeight: '600' }],
        h3: ['1.375rem', { lineHeight: '1.36', fontWeight: '600' }],
        body: ['1rem', { lineHeight: '1.625' }],
        sm: ['0.875rem', { lineHeight: '1.57' }],
        xs: ['0.75rem', { lineHeight: '1.5', fontWeight: '500' }],
      },
      // Flipkart marketplace, softened a touch for a premium feel (README deltas).
      borderRadius: {
        xs: '3px',
        sm: '7px',
        md: '9px',
        lg: '12px',
        xl: '16px',
        // Wellvia storefront radii (ported reference rounded-xl2 / rounded-xl3).
        xl2: '18px',
        xl3: '22px',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        lift: 'var(--shadow-lift)',
        glow: '0 0 0 1px rgba(40,116,240,0.35), 0 6px 20px rgba(40,116,240,0.2)',
        // Softer accent ring — use instead of glow when you want less saturation
        'glow-sm': '0 0 0 1px rgba(40,116,240,0.22), 0 3px 12px rgba(40,116,240,0.14)',
        // Success/danger glow for inline feedback states
        'glow-success': '0 0 0 1px rgba(56,142,60,0.35), 0 4px 16px rgba(56,142,60,0.15)',
        'glow-danger': '0 0 0 1px rgba(239,68,68,0.35), 0 4px 16px rgba(239,68,68,0.15)',
      },
      maxWidth: {
        content: '1200px',
      },
      keyframes: {
        shimmer: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        },
        marquee: {
          '0%': { transform: 'translate3d(0,0,0)' },
          '100%': { transform: 'translate3d(-50%,0,0)' },
        },
        // Fade in + rise — JS-free alternative when Framer Motion is not available
        fadeUp: {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        // Scale in from center — for modals, popovers, tooltips
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
        // Pulse ring — for live/attention indicators
        pulseRing: {
          '0%': { transform: 'scale(1)', opacity: '0.6' },
          '100%': { transform: 'scale(1.6)', opacity: '0' },
        },
        // ── Wellvia storefront motion (ported from the reference) ──────────
        rise: { '0%': { opacity: '0', transform: 'translateY(14px)' }, '100%': { opacity: '1', transform: 'none' } },
        slidein: { '0%': { transform: 'translateX(40px)', opacity: '0' }, '100%': { transform: 'translateX(0)', opacity: '1' } },
        dim: { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        spin360: { to: { transform: 'rotate(360deg)' } },
      },
      animation: {
        shimmer: 'shimmer 1.6s ease-in-out infinite',
        marquee: 'marquee 60s linear infinite',
        fadeUp: 'fadeUp 0.32s cubic-bezier(0.16, 1, 0.3, 1) both',
        scaleIn: 'scaleIn 0.2s cubic-bezier(0.22, 1, 0.36, 1) both',
        pulseRing: 'pulseRing 1.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        // Wellvia storefront animations.
        rise: 'rise .5s ease both',
        slidein: 'slidein .3s ease both',
        dim: 'dim .25s ease both',
        spin360: 'spin360 1s linear infinite',
      },
      // Transitioned properties used for performant hover-lift
      transitionProperty: {
        'lift': 'transform, box-shadow',
        'card': 'transform, box-shadow, border-color',
      },
    },
  },
  plugins: [],
};
