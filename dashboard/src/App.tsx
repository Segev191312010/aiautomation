import React, { Suspense, lazy, useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Layout from '@/components/layout/Layout'
import ErrorBoundary from '@/components/ui/ErrorBoundary'
import AuthGuard from '@/components/auth/AuthGuard'
import Dashboard from '@/pages/Dashboard'
import { useAlertStore, useBotStore } from '@/store'
import { fetchStatus } from '@/services/api'
import { APP_ROUTE_PATHS } from '@/utils/routes'
import AlertSoundPlayer from '@/components/alerts/AlertSoundPlayer'
import {
  NotificationProvider,
  useNotificationController,
} from '@/components/alerts/NotificationProvider'

const TradeBotPage = lazy(() => import('@/pages/TradeBotPage'))
const MarketPage = lazy(() => import('@/pages/MarketPage'))
const MarketRotationPage = lazy(() => import('@/pages/MarketRotationPage'))
const SimulationPage = lazy(() => import('@/pages/SimulationPage'))
const MarketsPage = lazy(() => import('@/pages/MarketsPage'))
const BacktestPage = lazy(() => import('@/pages/BacktestPage'))
const AlertsPage = lazy(() => import('@/pages/AlertsPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const StockProfilePage = lazy(() => import('@/pages/StockProfilePage'))
const AnalyticsPage = lazy(() => import('@/pages/AnalyticsPage'))
const RulesPage = lazy(() => import('@/pages/RulesPage'))
const AutopilotPage = lazy(() => import('@/pages/AutopilotPage'))
const ChartsPage = lazy(() => import('@/pages/ChartsPage'))

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
          <Route path={APP_ROUTE_PATHS.screener} element={<MarketsPage />} />
          <Route path={APP_ROUTE_PATHS.swing} element={<MarketsPage />} />
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

function StatusPoller() {
  const setStatus = useBotStore((s) => s.setStatus)

  useEffect(() => {
    const poll = async () => {
      try {
        const status = await fetchStatus()
        setStatus(status)
      } catch {
        /* backend offline */
      }
    }

    poll()
    const timer = setInterval(poll, 30_000)
    return () => clearInterval(timer)
  }, [setStatus])

  return null
}

function NotificationRuntime() {
  const preferences = useAlertStore((state) => state.notificationPrefs)
  const loadPreferences = useAlertStore((state) => state.loadNotificationPrefs)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let active = true
    void loadPreferences().then((success) => {
      if (active) setLoaded(success)
    })
    return () => {
      active = false
    }
  }, [loadPreferences])

  if (!loaded) return null
  return (
    <>
      <AlertSoundPlayer prefs={preferences} />
      <PushSubscriptionRuntime preferenceEnabled={preferences.browser_push} />
    </>
  )
}

function PushSubscriptionRuntime({ preferenceEnabled }: { preferenceEnabled: boolean }) {
  const { reconcile } = useNotificationController()

  useEffect(() => {
    void reconcile(preferenceEnabled)
  }, [preferenceEnabled, reconcile])

  return null
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthGuard>
        <NotificationProvider>
          <Layout>
            <StatusPoller />
            <NotificationRuntime />
            <AppRoutes />
          </Layout>
        </NotificationProvider>
      </AuthGuard>
    </BrowserRouter>
  )
}
