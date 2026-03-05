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
        // ── Modern Fintech palette ─────────────────────────────────────
        terminal: {
          bg:       '#0b0f1a',       // warm deep navy
          surface:  '#111827',       // slate-900
          elevated: '#1e293b',       // slate-800 — modals, hover
          border:   'rgba(148,163,184,0.12)', // soft glass border
          muted:    '#1e293b',       // subtle fills
          input:    '#0f172a',       // slate-950 — inputs

          text:     '#f1f5f9',       // slate-100
          dim:      '#94a3b8',       // slate-400
          ghost:    '#475569',       // slate-600

          green:    '#10b981',       // emerald-500
          'green-dim': '#065f46',    // emerald-900
          red:      '#ef4444',       // red-500
          'red-dim': '#7f1d1d',      // red-900

          blue:     '#6366f1',       // indigo-500 — primary accent
          'blue-dim': '#312e81',     // indigo-900

          amber:    '#f59e0b',       // warnings
          purple:   '#a78bfa',       // premium

          chart: {
            up:        '#10b981',
            down:      '#ef4444',
            grid:      '#1e293b',
            crosshair: '#334155',
          },
        },
      },
      boxShadow: {
        'terminal':    '0 1px 3px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.25)',
        'glass':       '0 4px 30px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.05)',
        'glass-lg':    '0 8px 40px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.06)',
        'glow-green':  '0 0 20px rgba(16,185,129,0.15)',
        'glow-red':    '0 0 20px rgba(239,68,68,0.15)',
        'glow-blue':   '0 0 20px rgba(99,102,241,0.2)',
      },
      borderRadius: {
        'xl':  '0.75rem',
        '2xl': '1rem',
        '3xl': '1.5rem',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'tick-up':    'tickUp 0.3s ease-out',
        'tick-down':  'tickDown 0.3s ease-out',
        'shimmer':    'shimmer 2s linear infinite',
        'fade-in':    'fadeIn 0.3s ease-out',
      },
      keyframes: {
        tickUp: {
          '0%':   { color: '#f1f5f9' },
          '30%':  { color: '#10b981' },
          '100%': { color: '#f1f5f9' },
        },
        tickDown: {
          '0%':   { color: '#f1f5f9' },
          '30%':  { color: '#ef4444' },
          '100%': { color: '#f1f5f9' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        fadeIn: {
          '0%':   { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}

export default config
