import React, { useEffect } from 'react'
import Layout from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import TradeBotPage from '@/pages/TradeBotPage'
import MarketPage from '@/pages/MarketPage'
import SimulationPage from '@/pages/SimulationPage'
import { useUIStore, useBotStore } from '@/store'
import { fetchStatus } from '@/services/api'

// ── Lazy pages (rules, settings) ─────────────────────────────────────────────

function RulesPage() {
  return (
    <div className="flex items-center justify-center h-64 text-terminal-ghost font-mono text-sm">
      Rules engine — coming soon
    </div>
  )
}

function SettingsPage() {
  return (
    <div className="flex items-center justify-center h-64 text-terminal-ghost font-mono text-sm">
      Settings — coming soon
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
    case 'simulation': return <SimulationPage />
    case 'rules':      return <RulesPage />
    case 'settings':   return <SettingsPage />
    default:           return <Dashboard />
  }
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const setStatus = useBotStore((s) => s.setStatus)

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
