import { useCallback, useEffect, useMemo, useState } from 'react'

import { useColorScheme } from '../hooks/useColorScheme'
import { useLiveData } from '../hooks/useLiveData'
import type { ScreenerRow, SelectedSymbol } from '../types'
import { AutopilotPanel } from './AutopilotPanel'
import { ManualOrdersPanel } from './ManualOrdersPanel'
import { OrdersPanel } from './OrdersPanel'
import { ScreenerTable } from './ScreenerTable'
import { SignalsFeed } from './SignalsFeed'
import { StatusBar } from './StatusBar'
import { TradingViewChart } from './TradingViewChart'

type LiveDashboardProps = {
  token: string
  onUnauthorized: () => void
  onLock: () => void
}

function topRow(rows: readonly ScreenerRow[] | undefined): ScreenerRow | undefined {
  return rows?.find((row) => row.hot === true) ?? rows?.[0]
}

/** Fill in a missing exchange (e.g. a signal for an unlisted symbol) from the screener when possible. */
function withExchange(selection: SelectedSymbol, rows: readonly ScreenerRow[] | undefined): SelectedSymbol {
  if (selection.exchange) return selection
  const match = rows?.find((row) => row.symbol === selection.symbol)
  return { symbol: selection.symbol, exchange: match?.primary_exchange ?? null }
}

export function LiveDashboard({ token, onUnauthorized, onLock }: LiveDashboardProps) {
  const [signalsDate, setSignalsDate] = useState('')
  const [selection, setSelection] = useState<SelectedSymbol | null>(null)
  const live = useLiveData(token, signalsDate, onUnauthorized)
  const theme = useColorScheme()
  const rows = live.screener.data?.rows

  // Pin the first ranked symbol once so ranking churn never reloads the chart by itself.
  const first = topRow(rows)
  useEffect(() => {
    if (selection === null && first) {
      setSelection({ symbol: first.symbol, exchange: first.primary_exchange ?? null })
    }
  }, [selection, first])

  const active = useMemo(() => (selection ? withExchange(selection, rows) : null), [selection, rows])
  const handleSelect = useCallback((next: SelectedSymbol) => setSelection(next), [])

  // The five manager polls usually fail together (one backend); one line says so.
  const managerProblem =
    live.managerStatus.error ??
    live.managerPlan.error ??
    live.managerPlans.error ??
    live.managerVerdicts.error ??
    live.managerSpend.error
  const problems = useMemo(
    () =>
      [
        live.status.error,
        live.screener.error,
        live.market.error,
        live.signals.error,
        live.orders.error,
        live.manualOrders.error,
        managerProblem === null ? null : `Autopilot: ${managerProblem}`,
      ].filter((problem): problem is string => Boolean(problem)),
    [
      live.status.error,
      live.screener.error,
      live.market.error,
      live.signals.error,
      live.orders.error,
      live.manualOrders.error,
      managerProblem,
    ],
  )
  const today = live.status.data?.session_date ?? null

  return (
    <div className="live-shell">
      <StatusBar
        status={live.status}
        market={live.market}
        lastPollAt={live.lastPollAt}
        paused={live.paused}
        problems={problems}
        onLock={onLock}
      />
      <main className="live-grid">
        <section className="panel screener-panel" aria-label="Live screener">
          <ScreenerTable
            snapshot={live.screener.data}
            error={live.screener.error}
            selectedSymbol={active?.symbol ?? null}
            onSelect={handleSelect}
          />
        </section>
        <section className="panel chart-panel" aria-label="Chart">
          <TradingViewChart symbol={active?.symbol ?? null} exchange={active?.exchange ?? null} theme={theme} />
        </section>
        <section className="panel signals-panel" aria-label="Signals">
          <SignalsFeed
            signals={live.signals.data}
            error={live.signals.error}
            date={signalsDate}
            today={today}
            onDateChange={setSignalsDate}
            selectedSymbol={active?.symbol ?? null}
            onSelect={handleSelect}
          />
        </section>
        <section className="panel orders-panel" aria-label="PAPER orders">
          <OrdersPanel
            orders={live.orders.data}
            error={live.orders.error}
            bridgeState={live.status.data?.bridge_state ?? null}
            today={today}
            onSelect={handleSelect}
          />
        </section>
        <section className="panel manual-panel" aria-label="Manual orders">
          <ManualOrdersPanel
            orders={live.manualOrders.data}
            error={live.manualOrders.error}
            onSelect={handleSelect}
          />
        </section>
        <section className="panel autopilot-panel" aria-label="Autopilot">
          <AutopilotPanel
            status={live.managerStatus}
            plan={live.managerPlan}
            plans={live.managerPlans}
            verdicts={live.managerVerdicts}
            spend={live.managerSpend}
          />
        </section>
      </main>
    </div>
  )
}
