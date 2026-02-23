import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', '"Cascadia Code"', 'ui-monospace', 'monospace'],
        sans: ['"Inter"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      colors: {
        // ── Bloomberg / Terminal palette ────────────────────────────────
        terminal: {
          bg:       '#080d18',   // deepest background
          surface:  '#0e1726',   // card / panel surface
          elevated: '#131f33',   // modals, dropdowns
          border:   '#1c2e4a',   // all borders
          muted:    '#243650',   // subtle fills
          input:    '#0a1525',   // input fields

          text:     '#dce8f5',   // primary text
          dim:      '#5f7a9d',   // secondary / muted text
          ghost:    '#384d6b',   // placeholder text

          green:    '#00e07a',   // ▲ gains, buy, positive
          'green-dim': '#00874a', // muted green
          red:      '#ff3d5a',   // ▼ losses, sell, negative
          'red-dim':   '#992438', // muted red

          blue:     '#4f91ff',   // accent / info
          'blue-dim':  '#1e3c6e', // muted blue

          amber:    '#f59e0b',   // warnings, alerts
          purple:   '#a78bfa',   // special / premium

          chart: {
            up:        '#00e07a',
            down:      '#ff3d5a',
            grid:      '#111f35',
            crosshair: '#2b4a7a',
          },
        },
      },
      boxShadow: {
        'terminal': '0 0 0 1px rgba(79,145,255,0.08), 0 4px 24px rgba(0,0,0,0.5)',
        'glow-green': '0 0 16px rgba(0,224,122,0.2)',
        'glow-red':   '0 0 16px rgba(255,61,90,0.2)',
        'glow-blue':  '0 0 16px rgba(79,145,255,0.2)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'tick-up':    'tickUp 0.3s ease-out',
        'tick-down':  'tickDown 0.3s ease-out',
      },
      keyframes: {
        tickUp: {
          '0%':   { color: '#dce8f5' },
          '30%':  { color: '#00e07a' },
          '100%': { color: '#dce8f5' },
        },
        tickDown: {
          '0%':   { color: '#dce8f5' },
          '30%':  { color: '#ff3d5a' },
          '100%': { color: '#dce8f5' },
        },
      },
    },
  },
  plugins: [],
}

export default config
