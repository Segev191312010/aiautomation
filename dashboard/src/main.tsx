import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ToastProvider from '@/components/ui/ToastProvider'
import './index.css'

// ── Theme initialisation (runs before React renders to prevent flash) ─────────
// Priority: localStorage → system preference → trading-desk default (dark)
function initTheme() {
  const stored = localStorage.getItem('theme') as 'light' | 'dark' | 'system' | null
  let resolved: 'light' | 'dark' = 'dark'  // trading-desk default

  if (stored === 'dark') {
    resolved = 'dark'
  } else if (stored === 'light') {
    resolved = 'light'
  } else if (stored === 'system' || stored === null) {
    resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'dark'
  }

  document.documentElement.setAttribute('data-theme', resolved)
}

initTheme()

// Re-apply theme when system preference changes (handles "system" mode)
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  const stored = localStorage.getItem('theme')
  if (stored === 'system' || stored === null) {
    // Trading-desk default is always dark
    document.documentElement.setAttribute('data-theme', 'dark')
  }
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </React.StrictMode>,
)
