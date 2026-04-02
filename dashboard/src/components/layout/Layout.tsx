import React from 'react'
import Sidebar from './Sidebar'
import Header from './Header'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useMarketData } from '@/hooks/useMarketData'

interface Props {
  children: React.ReactNode
}

export default function Layout({ children }: Props) {
  useWebSocket()
  useMarketData()

  return (
    <div className="relative flex h-screen w-screen overflow-hidden">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-[-12rem] top-[-10rem] h-[26rem] w-[26rem] rounded-full bg-[rgba(245,158,11,0.14)] blur-3xl" />
        <div className="absolute bottom-[-12rem] right-[-8rem] h-[24rem] w-[24rem] rounded-full bg-[rgba(56,189,248,0.1)] blur-3xl" />
      </div>

      <Sidebar />

      <div className="relative z-10 flex min-w-0 flex-1 flex-col overflow-hidden">
        <Header />
        <main className="relative flex-1 overflow-y-auto overflow-x-hidden px-4 pb-6 sm:px-6 lg:px-8">
          <div className="mx-auto flex min-h-full w-full max-w-[1720px] flex-col gap-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
