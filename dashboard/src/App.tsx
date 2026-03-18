import React, { Suspense, lazy, useEffect } from 'react'
import Layout from '@/components/layout/Layout'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import Dashboard from '@/pages/Dashboard'
import { useUIStore, useBotStore } from '@/store'
import { fetchStatus, fetchAuthToken, setAuthToken } from '@/services/api'

const TradeBotPage = lazy(() => import('@/pages/TradeBotPage'))
const MarketPage = lazy(() => import('@/pages/MarketPage'))
const MarketRotationPage = lazy(() => import('@/pages/MarketRotationPage'))
const SimulationPage = lazy(() => import('@/pages/SimulationPage'))
const ScreenerPage = lazy(() => import('@/pages/ScreenerPage'))
const BacktestPage = lazy(() => import('@/pages/BacktestPage'))
const AlertsPage = lazy(() => import('@/pages/AlertsPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const StockProfilePage = lazy(() => import('@/pages/StockProfilePage'))
const AnalyticsPage = lazy(() => import('@/pages/AnalyticsPage'))
const RulesPage = lazy(() => import('@/pages/RulesPage'))

function PageFallback() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="card rounded-lg shadow-card px-6 py-5 text-center">
        <p className="text-gray-800 font-sans text-sm font-semibold tracking-wide">
          Loading view
        </p>
        <p className="text-gray-500 font-sans text-xs mt-1">
          Preparing market workspace...
        </p>
      </div>
    </div>
  )
}

// ── Route → component map ─────────────────────────────────────────────────────

function PageSwitch() {
  const route = useUIStore((s) => s.activeRoute)
  let page: React.ReactNode

  switch (route) {
    case 'dashboard':  page = <Dashboard />; break
    case 'tradebot':   page = <TradeBotPage />; break
    case 'market':     page = <MarketPage />; break
    case 'rotation':   page = <MarketRotationPage />; break
    case 'screener':   page = <ScreenerPage />; break
    case 'stock':      page = <StockProfilePage />; break
    case 'simulation': page = <SimulationPage />; break
    case 'backtest':   page = <BacktestPage />; break
    case 'rules':      page = <RulesPage />; break
    case 'alerts':     page = <AlertsPage />; break
    case 'analytics':  page = <AnalyticsPage />; break
    case 'settings':   page = <SettingsPage />; break
    default:           page = <Dashboard />
  }

  return <Suspense fallback={<PageFallback />}>{page}</Suspense>
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
