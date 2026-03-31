import { useState, useEffect } from 'react'
import { wsMdService, type QuoteMsg } from '@/services/ws'
import { SECTOR_ETFS } from './constants'

export interface LivePrice {
  price: number
  prevPrice: number
  time: number
  source: string
}

export function useLiveSectorPrices(): Map<string, LivePrice> {
  const [prices, setPrices] = useState<Map<string, LivePrice>>(new Map())

  useEffect(() => {
    const unsubs: (() => void)[] = []

    for (const s of SECTOR_ETFS) {
      const unsub = wsMdService.subscribe(s.symbol, (msg: QuoteMsg) => {
        setPrices(prev => {
          const next = new Map(prev)
          const existing = prev.get(msg.symbol)
          next.set(msg.symbol, {
            price: msg.price,
            prevPrice: existing?.price ?? msg.price,
            time: msg.time ?? Date.now() / 1000,
            source: msg.source ?? 'ibkr',
          })
          return next
        })
      })
      unsubs.push(unsub)
    }

    return () => unsubs.forEach(u => u())
  }, [])

  return prices
}
