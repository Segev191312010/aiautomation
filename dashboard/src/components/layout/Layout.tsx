import React from 'react'
import Sidebar from './Sidebar'
import Header from './Header'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useMarketData } from '@/hooks/useMarketData'

interface Props {
  children: React.ReactNode
}

export default function Layout({ children }: Props) {
  // Wire global data subscriptions
  useWebSocket()
  useMarketData()

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[var(--bg-primary)]">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-5 bg-[var(--bg-secondary)] transition-colors duration-200">
          {children}
        </main>
      </div>
    </div>
  )
}
