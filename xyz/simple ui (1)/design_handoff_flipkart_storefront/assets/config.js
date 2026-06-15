/* Tailwind Play CDN config — extends with ShopWell / Flipkart marketplace tokens.
   Must be set immediately after the CDN <script> tag. */
tailwind.config = {
  theme: {
    extend: {
      colors: {
        bg: {
          base: 'rgb(var(--bg-base))',
          elevated: 'rgb(var(--bg-elevated))',
          sunken: 'rgb(var(--bg-sunken))',
        },
        ink: {
          primary: 'rgb(var(--ink-primary))',
          secondary: 'rgb(var(--ink-secondary))',
          tertiary: 'rgb(var(--ink-tertiary))',
          inverse: 'rgb(var(--ink-inverse))',
        },
        accent: { DEFAULT: '#2874F0', hover: '#1F63D6', press: '#1A55BA', mid: '#5C97F5' },
        cta: { DEFAULT: '#FB641E', hover: '#E85610', press: '#D44E0A' },
        cart: { DEFAULT: '#FF9F00', hover: '#F59300', press: '#E08800' },
        rating: { DEFAULT: '#388E3C' },
        success: { DEFAULT: '#388E3C' },
        danger: { DEFAULT: '#EF4444' },
      },
      fontFamily: {
        sans: ['Roboto', 'system-ui', '-apple-system', 'Segoe UI', 'Arial', 'sans-serif'],
      },
      borderRadius: { xs: '3px', sm: '7px', md: '9px', lg: '12px', xl: '16px' },
      boxShadow: {
        sm: 'var(--shadow-sm)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        lift: 'var(--shadow-lift)',
      },
      maxWidth: { content: '1248px' },
      lineClamp: { 2: '2', 3: '3' },
    },
  },
};
