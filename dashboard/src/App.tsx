import React, { useEffect } from 'react'
import Layout from '@/components/layout/Layout'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import Dashboard from '@/pages/Dashboard'
import TradeBotPage from '@/pages/TradeBotPage'
import MarketPage from '@/pages/MarketPage'
import SimulationPage from '@/pages/SimulationPage'
import ScreenerPage from '@/pages/ScreenerPage'
import BacktestPage from '@/pages/BacktestPage'
import AlertsPage from '@/pages/AlertsPage'
import SettingsPage from '@/pages/SettingsPage'
import StockProfilePage from '@/pages/StockProfilePage'
import { useUIStore, useBotStore } from '@/store'
import { fetchStatus, fetchAuthToken, setAuthToken } from '@/services/api'

// ── Lazy pages (rules) ──────────────────────────────────────────────────────

function RulesPage() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="glass rounded-2xl shadow-glass px-8 py-6 text-center">
        <p className="gradient-text font-sans text-lg font-semibold tracking-wide">
          Rules Engine
        </p>
        <p className="text-terminal-ghost font-sans text-sm mt-1">
          Coming soon
        </p>
      </div>
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
    case 'stock':      return <StockProfilePage />
    case 'simulation': return <SimulationPage />
    case 'backtest':   return <BacktestPage />
    case 'rules':      return <RulesPage />
    case 'alerts':     return <AlertsPage />
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
      } catch { /* backend offline */ }
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
