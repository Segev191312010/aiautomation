import React, { Suspense, lazy, useCallback, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Layout from '@/components/layout/Layout'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import Dashboard from '@/pages/Dashboard'
import { useBotStore, useSessionStore } from '@/store'
import { bootstrapSession, fetchStatus } from '@/services/api'
import AuthGuard from '@/components/auth/AuthGuard'
import { APP_ROUTE_PATHS } from '@/utils/routes'

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
const AutopilotPage = lazy(() => import('@/pages/AutopilotPage'))
const ChartsPage = lazy(() => import('@/pages/ChartsPage'))
const SwingDashboardPage = lazy(() => import('@/pages/SwingDashboardPage'))

function PageFallback() {
  return (
    <div className="flex h-64 items-center justify-center">
      <div className="card rounded-lg px-6 py-5 text-center shadow-card">
        <p className="text-sm font-semibold tracking-wide text-gray-800 font-sans">
          Loading view
        </p>
        <p className="mt-1 text-xs text-gray-500 font-sans">
          Preparing market workspace...
        </p>
      </div>
    </div>
  )
}

function AppRoutes() {
  const location = useLocation()

  return (
    <ErrorBoundary key={location.pathname}>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path={APP_ROUTE_PATHS.dashboard} element={<Dashboard />} />
          <Route path={APP_ROUTE_PATHS.tradebot} element={<TradeBotPage />} />
          <Route path={APP_ROUTE_PATHS.market} element={<MarketPage />} />
          <Route path={APP_ROUTE_PATHS.charts} element={<ChartsPage />} />
          <Route path={APP_ROUTE_PATHS.rotation} element={<MarketRotationPage />} />
          <Route path={APP_ROUTE_PATHS.screener} element={<ScreenerPage />} />
          <Route path={APP_ROUTE_PATHS.swing} element={<SwingDashboardPage />} />
          <Route path={APP_ROUTE_PATHS.stock} element={<StockProfilePage />} />
          <Route path={APP_ROUTE_PATHS.simulation} element={<SimulationPage />} />
          <Route path={APP_ROUTE_PATHS.backtest} element={<BacktestPage />} />
          <Route path={APP_ROUTE_PATHS.rules} element={<RulesPage />} />
          <Route path={APP_ROUTE_PATHS.alerts} element={<AlertsPage />} />
          <Route path={APP_ROUTE_PATHS.analytics} element={<AnalyticsPage />} />
          <Route path={APP_ROUTE_PATHS.advisor} element={<AutopilotPage />} />
          <Route path={APP_ROUTE_PATHS.settings} element={<SettingsPage />} />
          <Route path="*" element={<Navigate to={APP_ROUTE_PATHS.dashboard} replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}

export default function App() {
  const setStatus = useBotStore((s) => s.setStatus)
  const sessionStatus = useSessionStore((s) => s.status)
  const beginBootstrap = useSessionStore((s) => s.beginBootstrap)
  const setSession = useSessionStore((s) => s.setSession)
  const failBootstrap = useSessionStore((s) => s.failBootstrap)

  const startBootstrap = useCallback(async () => {
    beginBootstrap()
    try {
      const session = await bootstrapSession()
      setSession({ accessToken: session.access_token, expiresAt: session.expires_at })
      if (window.location.pathname === '/session-expired') {
        window.history.replaceState(window.history.state, '', APP_ROUTE_PATHS.dashboard)
        window.dispatchEvent(new PopStateEvent('popstate'))
      }
    } catch (error) {
      const detail = error instanceof Error ? error.message : 'Unknown bootstrap failure'
      failBootstrap(`Session bootstrap failed: ${detail}`)
    }
  }, [beginBootstrap, failBootstrap, setSession])

  useEffect(() => {
    void startBootstrap()
  }, [startBootstrap])

  useEffect(() => {
    if (sessionStatus !== 'authenticated') return undefined

    const refreshStatus = async () => {
      try {
        setStatus(await fetchStatus())
      } catch {
        // The shared client owns session-loss handling. Transient backend
        // failures leave the last known status visible until the next poll.
      }
    }

    void refreshStatus()
    const timer = setInterval(async () => {
      await refreshStatus()
    }, 30_000)

    return () => clearInterval(timer)
  }, [sessionStatus, setStatus])

  return (
    <BrowserRouter>
      <AuthGuard onRetry={startBootstrap}>
        <Layout>
          <AppRoutes />
        </Layout>
      </AuthGuard>
    </BrowserRouter>
  )
}
