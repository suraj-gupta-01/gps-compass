/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', 'monospace'],
        ui: ['"DM Sans"', 'sans-serif'],
      },
      colors: {
        surface: { 900:'#080c10', 800:'#0d1117', 700:'#161b22' },
        accent: { cyan:'#00d4ff', green:'#00ff88', amber:'#ffb800', red:'#ff3b5c', purple:'#9b59ff' },
      },
    },
  },
  plugins: [],
}
