import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // Use data-theme attribute for dark/light variant switching
  darkMode: ['attribute', '[data-theme="dark"]'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['"JetBrains Mono"', '"Fira Code"', '"Cascadia Code"', 'ui-monospace', 'monospace'],
        sans: ['"Inter"', 'ui-sans-serif', 'system-ui', '-apple-system', 'sans-serif'],
      },
      colors: {
        // ── CSS-variable-backed semantic tokens ──────────────────────────
        // These automatically follow the active theme (light/dark).
        theme: {
          bg:        'var(--bg-primary)',
          surface:   'var(--bg-secondary)',
          card:      'var(--bg-card)',
          hover:     'var(--bg-hover)',
          input:     'var(--bg-input)',
          sidebar:   'var(--bg-sidebar)',
          header:    'var(--bg-header)',
          text:      'var(--text-primary)',
          dim:       'var(--text-secondary)',
          muted:     'var(--text-muted)',
          border:    'var(--border)',
          accent:    'var(--accent)',
          'accent-hover': 'var(--accent-hover)',
          success:   'var(--success)',
          danger:    'var(--danger)',
          warning:   'var(--warning)',
        },
        // ── Warm Cream palette (legacy — light mode hardcoded values) ────
        terminal: {
          bg:       '#FAF8F5',       // warm cream background
          surface:  '#FFFFFF',       // pure white cards
          elevated: '#F5F3F0',       // slightly darker cream
          border:   '#E8E4DF',       // warm gray border
          muted:    '#F0EDE8',       // skeleton/placeholder fills
          input:    '#FFFFFF',       // white inputs

          text:     '#1A1A2E',       // near-black
          dim:      '#6B7280',       // medium gray
          ghost:    '#9CA3AF',       // light gray

          green:    '#16A34A',       // green-600
          'green-dim': '#DCFCE7',    // green background tint
          red:      '#DC2626',       // red-600
          'red-dim': '#FEE2E2',      // red background tint

          blue:     '#4F46E5',       // indigo-600 — primary accent
          'blue-dim': '#EEF2FF',     // indigo-50

          amber:    '#D97706',       // amber-600
          purple:   '#7C3AED',       // purple-600

          chart: {
            up:        '#16A34A',
            down:      '#DC2626',
            grid:      '#F0EDE8',
            crosshair: '#D1D5DB',
          },
        },
      },
      boxShadow: {
        'terminal':    '0 1px 3px rgba(0,0,0,0.2), 0 1px 2px rgba(0,0,0,0.1)',
        'card':        '0 1px 3px rgba(0,0,0,0.2), 0 1px 2px rgba(0,0,0,0.1)',
        'glass':       '0 1px 3px rgba(0,0,0,0.2), 0 1px 2px rgba(0,0,0,0.1)',
        'card-lg':     '0 4px 16px rgba(0,0,0,0.3)',
        'glass-lg':    '0 4px 16px rgba(0,0,0,0.3)',
        'dropdown':    '0 8px 28px rgba(0,0,0,0.4)',
        'glow-green':  '0 0 20px rgba(52,211,153,0.15)',
        'glow-red':    '0 0 20px rgba(248,113,113,0.15)',
        'glow-blue':   '0 0 20px rgba(79,70,229,0.15)',
        'glow-accent': '0 0 20px rgba(245,158,11,0.15)',
      },
      borderRadius: {
        'xl':  '0.75rem',
        '2xl': '1rem',
        '3xl': '1.5rem',
      },
      animation: {
        'pulse-slow':   'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'tick-up':      'tickUp 0.3s ease-out',
        'tick-down':    'tickDown 0.3s ease-out',
        'shimmer':      'shimmer 2s linear infinite',
        'fade-in':      'fadeIn 0.3s ease-out',
        'fade-in-up':   'fadeInUp 0.3s ease-out forwards',
      },
      keyframes: {
        tickUp: {
          '0%':   { color: 'var(--text-primary)' },
          '30%':  { color: 'var(--success)' },
          '100%': { color: 'var(--text-primary)' },
        },
        tickDown: {
          '0%':   { color: 'var(--text-primary)' },
          '30%':  { color: 'var(--danger)' },
          '100%': { color: 'var(--text-primary)' },
        },
        shimmer: {
          '0%':   { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        fadeIn: {
          '0%':   { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeInUp: {
          '0%':   { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
}

export default config
