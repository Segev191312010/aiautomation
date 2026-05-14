import React from 'react'
import Sidebar from './Sidebar'
import Header from './Header'
import AlertToaster from '@/components/common/AlertToaster'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useMarketData } from '@/hooks/useMarketData'
import { useAlerts } from '@/hooks/useAlerts'

interface Props {
  children: React.ReactNode
}

export default function Layout({ children }: Props) {
  // Wire global data subscriptions
  useWebSocket()
  useMarketData()
  useAlerts()

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-terminal-bg">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-4">
          {children}
        </main>
      </div>
      <AlertToaster />
    </div>
  )
}
