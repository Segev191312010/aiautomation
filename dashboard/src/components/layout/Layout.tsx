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
    <div className="flex h-screen w-screen overflow-hidden bg-terminal-bg">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 gradient-surface transition-colors duration-300">
          {children}
        </main>
      </div>
    </div>
  )
}
