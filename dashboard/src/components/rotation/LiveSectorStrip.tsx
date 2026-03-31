import { useMemo } from 'react'
import clsx from 'clsx'
import { fmtPct, fmtPrice, pctColor } from '@/utils/formatters'
import { SECTOR_ETFS, Q_COLORS, ROTATION_ARROWS } from './constants'
import type { LivePrice } from './useLiveSectorPrices'
import type { SectorRotation } from '@/types'

interface LiveSectorStripProps {
  rotation: SectorRotation[]
  livePrices: Map<string, LivePrice>
  selectedSector: string
  onSelectSector: (s: string) => void
}

export function LiveSectorStrip({
  rotation,
  livePrices,
  selectedSector,
  onSelectSector,
}: LiveSectorStripProps) {
  const rotMap = useMemo(() => new Map(rotation.map(r => [r.symbol, r])), [rotation])

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-11 gap-2">
      {SECTOR_ETFS.map(({ symbol, name }) => {
        const rot = rotMap.get(symbol)
        const live = livePrices.get(symbol)
        const price = live?.price ?? rot?.price ?? null
        const perf1m = rot?.perf_1m ?? null
        const quadrant = rot?.quadrant
        const isSelected = symbol === selectedSector
        const flash = live && live.price !== live.prevPrice
          ? live.price > live.prevPrice ? 'ring-1 ring-emerald-500/40' : 'ring-1 ring-red-500/40'
          : ''

        return (
          <button
            key={symbol}
            type="button"
            onClick={() => onSelectSector(symbol)}
            className={clsx(
              'rounded-lg border p-2.5 text-left transition-all duration-200 hover:border-zinc-700',
              isSelected
                ? 'border-blue-500/50 bg-blue-500/5'
                : 'border-zinc-700/50 bg-zinc-900/40',
              flash,
            )}
          >
            <div className="flex items-center justify-between gap-1 mb-1">
              <span className="font-mono text-xs font-bold text-zinc-600">{symbol}</span>
              {quadrant && (
                <span className={clsx('text-[9px]', Q_COLORS[quadrant].text)}>
                  {ROTATION_ARROWS[quadrant]}
                </span>
              )}
            </div>
            <div className="text-[10px] text-zinc-400 truncate mb-1.5">{name}</div>
            <div className="flex items-baseline justify-between gap-1">
              <span className="font-mono text-sm font-semibold text-zinc-700 tabular-nums">
                {price != null ? fmtPrice(price) : '--'}
              </span>
              {perf1m != null && (
                <span className={clsx('font-mono text-[10px] font-medium tabular-nums', pctColor(perf1m))}>
                  {fmtPct(perf1m, 1)}
                </span>
              )}
            </div>
            {live && (
              <div className="mt-1 flex items-center gap-1">
                <span className="h-1 w-1 rounded-full bg-emerald-500 animate-pulse" />
                <span className="text-[8px] text-zinc-400 uppercase tracking-wider">LIVE</span>
              </div>
            )}
          </button>
        )
      })}
    </div>
  )
}
