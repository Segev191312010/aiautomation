import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ToastProvider from '@/components/ui/ToastProvider'
import './index.css'

// ── Theme initialisation (runs before React renders to prevent flash) ─────────
// Priority: localStorage → system preference → default (light)
function initTheme() {
  const stored = localStorage.getItem('theme') as 'light' | 'dark' | 'system' | null
  let resolved: 'light' | 'dark' = 'light'

  if (stored === 'dark') {
    resolved = 'dark'
  } else if (stored === 'light') {
    resolved = 'light'
  } else if (stored === 'system' || stored === null) {
    resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }

  document.documentElement.setAttribute('data-theme', resolved)
}

initTheme()

// Re-apply theme when system preference changes (handles "system" mode)
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  const stored = localStorage.getItem('theme')
  if (stored === 'system' || stored === null) {
    const resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    document.documentElement.setAttribute('data-theme', resolved)
  }
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ToastProvider>
      <App />
    </ToastProvider>
  </React.StrictMode>,
)
