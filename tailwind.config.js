/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './apps/api/lottina_api/templates/**/*.{html,js}',
    './apps/api/lottina_api/static/**/*.{html,js,ts,jsx,tsx,vue}',
  ],
  theme: {
    container: {
      center: true,
      padding: {
        DEFAULT: '1.5rem',
        sm: '2rem',
        lg: '3rem',
        xl: '4rem',
      },
    },
    extend: {
      colors: {
        lottina: {
          primary: '#158c59',
          secondary: '#8ad35b',
          accent: '#ebb5f9',
          highlight: '#edd921',
          black: '#000000',
          white: '#ffffff',
          background: '#071c17',
        },
      },
      fontFamily: {
        lottina: ['"DM Sans"', 'sans-serif'],
        grotesk: ['"Bricolage Grotesk"', 'sans-serif'],
      },
      spacing: {
        gutter: '1.5rem',
        card: '1.25rem',
        section: '6.5rem',
        'section-tight': '4rem',
      },
      borderRadius: {
        brand: '1.5rem',
        soft: '1rem',
        pill: '9999px',
      },
      boxShadow: {
        'hero-depth': '0 25px 50px rgba(18, 19, 42, 0.35)',
        'card-soft': '0 12px 26px rgba(0, 0, 0, 0.18)',
        'card-strong': '0 22px 40px rgba(17, 45, 42, 0.35)',
        'cta-glow': '0 30px 60px rgba(139, 232, 79, 0.35)',
      },
      backgroundImage: {
        'feature-primary':
          'linear-gradient(140deg, rgba(18,63,59,0.92), rgba(32,101,92,0.85))',
        'feature-secondary':
          'linear-gradient(140deg, rgba(32,101,92,0.9), rgba(52,140,120,0.85))',
        'feature-accent':
          'linear-gradient(140deg, rgba(139,232,79,0.9), rgba(255,229,107,0.85))',
      },
      keyframes: {
        'flash-slide-in': {
          '0%': { transform: 'translateX(120%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        'flash-slide-out': {
          '0%': { transform: 'translateX(0)', opacity: '1' },
          '100%': { transform: 'translateX(120%)', opacity: '0' },
        },
      },
      animation: {
        'flash-in': 'flash-slide-in 0.55s ease forwards',
        'flash-out': 'flash-slide-out 0.55s ease forwards',
      },
    },
  },
  plugins: [],
};
