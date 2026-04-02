/**
 * useCrosshairSync — syncs the vertical crosshair line across N chart panes.
 *
 * Each pane provides its IChartApi, primary ISeriesApi, and a time→price Map
 * (needed because setCrosshairPosition requires all 3 args: price, time, series).
 *
 * On hover in any pane, all other panes show a matching vertical crosshair.
 * On mouse leave, all panes clear their crosshair.
 * Horizontal crosshair lines should be hidden on sub-panes via chart options.
 */
import { useEffect, useRef } from 'react'
import type { IChartApi, MouseEventParams } from 'lightweight-charts'

type CrosshairSeries = Parameters<IChartApi['setCrosshairPosition']>[2]

export interface ChartPane {
  chart:  IChartApi
  series: CrosshairSeries
  data:   Map<number, number>  // time (unix seconds) → price value
}

/**
 * Syncs crosshair position across all provided panes.
 * Panes may be null (not yet mounted / toggled off) — they're skipped.
 */
export function useCrosshairSync(panes: (ChartPane | null)[]): void {
  const syncingRef = useRef(false)
  // Keep latest panes in a ref so handlers always read fresh data maps
  // without needing to re-subscribe on every data change.
  const panesRef = useRef(panes)
  panesRef.current = panes

  // Extract chart instances as stable dependency (only re-subscribe when charts change)
  const chartKey = panes.map((p) => (p ? '1' : '0')).join('')

  useEffect(() => {
    const currentPanes = panesRef.current
    const activePanes = currentPanes.filter((p): p is ChartPane => p !== null)
    if (activePanes.length < 2) return

    const unsubs: (() => void)[] = []

    for (const source of activePanes) {
      // Capture the source chart reference for identity comparison
      const sourceChart = source.chart

      const handler = (param: MouseEventParams) => {
        if (syncingRef.current) return
        syncingRef.current = true

        try {
          const time = param.time as number | undefined
          // Read latest panes from ref for up-to-date data maps
          const latest = panesRef.current.filter((p): p is ChartPane => p !== null)

          for (const target of latest) {
            // Skip the source pane by chart identity (not index)
            if (target.chart === sourceChart) continue

            if (!time) {
              try { target.chart.clearCrosshairPosition() } catch { /* */ }
              continue
            }

            const price = target.data.get(time)
            if (price !== undefined) {
              try {
                target.chart.setCrosshairPosition(
                  price,
                  time as unknown as Parameters<typeof target.chart.setCrosshairPosition>[1],
                  target.series,
                )
              } catch { /* stale chart */ }
            } else {
              try { target.chart.clearCrosshairPosition() } catch { /* */ }
            }
          }
        } finally {
          syncingRef.current = false
        }
      }

      sourceChart.subscribeCrosshairMove(handler)
      unsubs.push(() => {
        try { sourceChart.unsubscribeCrosshairMove(handler) } catch { /* */ }
      })
    }

    return () => {
      unsubs.forEach((fn) => fn())
    }
  // Only re-subscribe when chart presence changes (mount/unmount)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chartKey])
}
