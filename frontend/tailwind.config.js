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
          // shadcn/ui token — used by the ghost/outline variants in button-base.jsx.
          foreground: 'hsl(var(--accent-foreground))',
        },
        success: '#22C55E',
        warning: '#F59E0B',
        danger: '#EF4444',

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
        glow: '0 0 0 1px rgba(99,102,241,0.4), 0 8px 32px rgba(99,102,241,0.25)',
      },
      maxWidth: {
        content: '1200px',
      },
      keyframes: {
        shimmer: {
          '100%': { transform: 'translateX(100%)' },
        },
        marquee: {
          '0%': { transform: 'translate3d(0,0,0)' },
          '100%': { transform: 'translate3d(-50%,0,0)' },
        },
      },
      animation: {
        shimmer: 'shimmer 1.4s infinite',
        marquee: 'marquee 60s linear infinite',
      },
    },
  },
  plugins: [],
};
