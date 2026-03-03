import React, { useEffect } from 'react'
import Layout from '@/components/layout/Layout'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import Dashboard from '@/pages/Dashboard'
import TradeBotPage from '@/pages/TradeBotPage'
import MarketPage from '@/pages/MarketPage'
import SimulationPage from '@/pages/SimulationPage'
import ScreenerPage from '@/pages/ScreenerPage'
import BacktestPage from '@/pages/BacktestPage'
import SettingsPage from '@/pages/SettingsPage'
import { useUIStore, useBotStore } from '@/store'
import { fetchStatus, fetchAuthToken, setAuthToken } from '@/services/api'

// ── Lazy pages (rules) ──────────────────────────────────────────────────────

function RulesPage() {
  return (
    <div className="flex items-center justify-center h-64 text-terminal-ghost font-mono text-sm">
      Rules engine — coming soon
    </div>
  )
}

// ── Route → component map ─────────────────────────────────────────────────────

function PageSwitch() {
  const route = useUIStore((s) => s.activeRoute)

  switch (route) {
    case 'dashboard':  return <Dashboard />
    case 'tradebot':   return <TradeBotPage />
    case 'market':     return <MarketPage />
    case 'screener':   return <ScreenerPage />
    case 'simulation': return <SimulationPage />
    case 'backtest':   return <BacktestPage />
    case 'rules':      return <RulesPage />
    case 'settings':   return <SettingsPage />
    default:           return <Dashboard />
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const setStatus = useBotStore((s) => s.setStatus)

  // Bootstrap auth token + system status on mount
  useEffect(() => {
    const bootstrap = async () => {
      // Fetch demo token on init
      try {
        const { access_token } = await fetchAuthToken()
        setAuthToken(access_token)
      } catch { /* backend offline */ }

      // Fetch system status
      try {
        const status = await fetchStatus()
        setStatus(status)
      } catch { /* backend offline — mock mode */ }
    }
    bootstrap()
    const t = setInterval(async () => {
      try {
        const status = await fetchStatus()
        setStatus(status)
      } catch { /* ignore */ }
    }, 30_000)
    return () => clearInterval(t)
  }, [setStatus])

  return (
    <Layout>
      <ErrorBoundary>
        <PageSwitch />
      </ErrorBoundary>
    </Layout>
  )
}
