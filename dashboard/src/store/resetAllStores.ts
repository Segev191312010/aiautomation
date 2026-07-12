import type { StoreApi } from 'zustand'
import { useAccountStore } from './accountStore'
import { useAnalyticsStore } from './analyticsStore'
import { useAlertStore } from './alertStore'
import { useAutopilotStore } from './autopilotStore'
import { useBacktestStore } from './backtestStore'
import { useBotStore } from './botStore'
import { useDiagnosticsStore } from './diagnosticsStore'
import { useDrawingStore } from './drawingStore'
import { useMarketStore } from './marketStore'
import { useRiskStore } from './riskStore'
import { useScreenerStore } from './screenerStore'
import { useSettingsStore } from './settingsStore'
import { useSimStore } from './simStore'
import { useStockProfileStore } from './stockProfileStore'
import { useSwingStore } from './swingStore'
import { useUIStore } from './uiStore'

function restoreInitialState<T>(store: StoreApi<T>) {
  store.setState(store.getInitialState(), true)
}

/** Clear every in-memory domain store when an operator session is lost. */
export function resetAllStores() {
  restoreInitialState(useAccountStore)
  restoreInitialState(useAnalyticsStore)
  restoreInitialState(useAlertStore)
  restoreInitialState(useAutopilotStore)
  restoreInitialState(useBacktestStore)
  restoreInitialState(useBotStore)
  restoreInitialState(useDiagnosticsStore)
  restoreInitialState(useDrawingStore)
  restoreInitialState(useMarketStore)
  restoreInitialState(useRiskStore)
  restoreInitialState(useScreenerStore)
  restoreInitialState(useSettingsStore)
  restoreInitialState(useSimStore)
  restoreInitialState(useStockProfileStore)
  restoreInitialState(useSwingStore)
  restoreInitialState(useUIStore)
}
