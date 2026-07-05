import React, { useEffect } from 'react'
import Layout from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import TradeBotPage from '@/pages/TradeBotPage'
import MarketPage from '@/pages/MarketPage'
import SimulationPage from '@/pages/SimulationPage'
import AutopilotPage from '@/pages/AutopilotPage'
import AiSystemPage from '@/pages/AiSystemPage'
import RulesPage from '@/pages/RulesPage'
import SettingsPage from '@/pages/SettingsPage'
import { useUIStore, useBotStore } from '@/store'
import { fetchStatus, getAuthToken, setAuthToken } from '@/services/api'
import { fetchAuthToken } from '@/services/auth'

// ── Route → component map ─────────────────────────────────────────────────────

function PageSwitch() {
  const route = useUIStore((s) => s.activeRoute)

  switch (route) {
    case 'dashboard':  return <Dashboard />
    case 'tradebot':   return <TradeBotPage />
    case 'market':     return <MarketPage />
    case 'simulation': return <SimulationPage />
    case 'autopilot':  return <AutopilotPage />
    case 'aisystem':   return <AiSystemPage />
    case 'rules':      return <RulesPage />
    case 'settings':   return <SettingsPage />
    default:           return <Dashboard />
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const setStatus = useBotStore((s) => s.setStatus)

  // Bootstrap an auth token once on mount so protected endpoints (autopilot,
  // rules) don't 401. Public endpoints (status) work regardless of this.
  useEffect(() => {
    let cancelled = false
    const boot = async () => {
      if (getAuthToken()) return
      try {
        const { access_token } = await fetchAuthToken()
        if (!cancelled) setAuthToken(access_token)
      } catch { /* no bootstrap secret / backend offline — protected views show a notice */ }
    }
    boot()
    return () => { cancelled = true }
  }, [])

  // Bootstrap system status on mount
  useEffect(() => {
    const load = async () => {
      try {
        const status = await fetchStatus()
        setStatus(status)
      } catch { /* backend offline — mock mode */ }
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
