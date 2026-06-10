/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
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
        // Brand + status colors are constant across both themes.
        accent: {
          DEFAULT: '#6366F1',
          hover: '#7C7FF5',
          press: '#5457D6',
          // Mid-ramp of the gradient — useful as a standalone tint
          mid: '#818CF8',
          // Light ramp — for tinted backgrounds in tonal badges
          soft: 'rgba(99, 102, 241, 0.12)',
          // shadcn/ui token — used by the ghost/outline variants in button-base.jsx.
          foreground: 'hsl(var(--accent-foreground))',
        },
        success: {
          DEFAULT: '#22C55E',
          soft: 'rgba(34, 197, 94, 0.12)',
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
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
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
      borderRadius: {
        xs: '8px',
        sm: '12px',
        md: '16px',
        lg: '24px',
        xl: '32px',
      },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        lift: 'var(--shadow-lift)',
        glow: '0 0 0 1px rgba(99,102,241,0.4), 0 8px 32px rgba(99,102,241,0.25)',
        // Softer accent ring — use instead of glow when you want less saturation
        'glow-sm': '0 0 0 1px rgba(99,102,241,0.25), 0 4px 16px rgba(99,102,241,0.15)',
        // Success/danger glow for inline feedback states
        'glow-success': '0 0 0 1px rgba(34,197,94,0.35), 0 4px 16px rgba(34,197,94,0.15)',
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
      },
      animation: {
        shimmer: 'shimmer 1.6s ease-in-out infinite',
        marquee: 'marquee 60s linear infinite',
        fadeUp: 'fadeUp 0.32s cubic-bezier(0.16, 1, 0.3, 1) both',
        scaleIn: 'scaleIn 0.2s cubic-bezier(0.22, 1, 0.36, 1) both',
        pulseRing: 'pulseRing 1.4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
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
