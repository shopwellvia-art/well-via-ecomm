/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        page: '#DED7C9',     // outer canvas
        bg: '#ECE8DE',       // paper background
        card: '#FFFDF8',     // cards / surfaces
        green: '#183A2E',    // primary
        greenh: '#10291F',   // primary hover
        ink: '#1E1E1A',      // main text
        muted: '#6F6A60',    // secondary text
        line: '#D8D0C4',     // borders
        gold: '#B49A63',     // accent
      },
      fontFamily: {
        display: ['Cinzel', 'serif'],            // wordmark
        serif: ['"Cormorant Garamond"', 'Georgia', 'serif'], // editorial headings
        sans: ['Jost', 'system-ui', 'sans-serif'],          // body
      },
      borderRadius: {
        xl2: '18px',
        xl3: '22px',
      },
      keyframes: {
        rise: { '0%': { opacity: 0, transform: 'translateY(14px)' }, '100%': { opacity: 1, transform: 'none' } },
        slidein: { '0%': { transform: 'translateX(40px)', opacity: 0 }, '100%': { transform: 'translateX(0)', opacity: 1 } },
        dim: { '0%': { opacity: 0 }, '100%': { opacity: 1 } },
      },
      animation: {
        rise: 'rise .5s ease both',
        slidein: 'slidein .3s ease both',
        dim: 'dim .25s ease both',
      },
    },
  },
  plugins: [],
};
