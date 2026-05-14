import React, { useEffect } from 'react'
import Layout from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import TradeBotPage from '@/pages/TradeBotPage'
import MarketPage from '@/pages/MarketPage'
import SimulationPage from '@/pages/SimulationPage'
import RulesPage from '@/pages/RulesPage'
import SettingsPage from '@/pages/SettingsPage'
import ScreenerPage from '@/pages/ScreenerPage'
import AlertsPage from '@/pages/AlertsPage'
import { useUIStore, useBotStore } from '@/store'
import { fetchStatus } from '@/services/api'

// ── Route → component map ─────────────────────────────────────────────────────

function PageSwitch() {
  const route = useUIStore((s) => s.activeRoute)

  switch (route) {
    case 'dashboard':  return <Dashboard />
    case 'tradebot':   return <TradeBotPage />
    case 'market':     return <MarketPage />
    case 'screener':   return <ScreenerPage />
    case 'alerts':     return <AlertsPage />
    case 'simulation': return <SimulationPage />
    case 'rules':      return <RulesPage />
    case 'settings':   return <SettingsPage />
    default:           return <Dashboard />
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const setStatus = useBotStore((s) => s.setStatus)

  // Bootstrap system status on mount. fetchStatus() falls back to mockBackend
  // automatically when the real server is unreachable, so this always resolves.
  useEffect(() => {
    const load = async () => {
      try {
        const status = await fetchStatus()
        setStatus(status)
      } catch {
        /* never — fetchStatus has a fallback */
      }
    }
    load()
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [setStatus])

  return (
    <Layout>
      <PageSwitch />
    </Layout>
  )
}
