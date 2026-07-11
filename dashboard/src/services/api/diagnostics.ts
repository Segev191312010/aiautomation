import type {
  DiagnosticIndicator,
  DiagnosticIndicatorHistoryPoint,
  DiagnosticMarketMap,
  DiagnosticNewsArticle,
  DiagnosticOverview,
  DiagnosticRefreshRun,
  DiagnosticSectorProjection,
} from '@/types'
import { get, postWithStatus } from './client'

export const fetchDiagnosticsOverview = (lookbackDays: 90 | 180 | 365 = 90) =>
  get<DiagnosticOverview>(`/api/diagnostics/overview?lookback_days=${lookbackDays}`)

export const fetchDiagnosticsIndicators = () =>
  get<DiagnosticIndicator[]>('/api/diagnostics/indicators')

export const fetchDiagnosticsIndicator = (code: string) =>
  get<DiagnosticIndicator>(`/api/diagnostics/indicators/${code}`)

export const fetchDiagnosticsIndicatorHistory = (code: string, days = 365) =>
  get<DiagnosticIndicatorHistoryPoint[]>(`/api/diagnostics/indicators/${code}/history?days=${days}`)

export const fetchDiagnosticsMarketMap = (days = 5) =>
  get<DiagnosticMarketMap[]>(`/api/diagnostics/market-map?days=${days}`)

export const fetchDiagnosticsSectorProjectionsLatest = (lookbackDays: 90 | 180 | 365 = 90) =>
  get<DiagnosticSectorProjection>(`/api/diagnostics/sector-projections/latest?lookback_days=${lookbackDays}`)

export const fetchDiagnosticsSectorProjectionsHistory = (days = 365) =>
  get<DiagnosticSectorProjection[]>(`/api/diagnostics/sector-projections/history?days=${days}`)

export const fetchDiagnosticsNews = (hours = 24, limit = 200) =>
  get<DiagnosticNewsArticle[]>(`/api/diagnostics/news?hours=${hours}&limit=${limit}`)

export async function runDiagnosticsRefresh(): Promise<
  | { status: 202; data: { run_id: number; status: string } }
  | { status: 409; data: { run_id: number; locked_by: string; lock_expires_at: number } }
> {
  return postWithStatus('/api/diagnostics/refresh', undefined) as Promise<
    | { status: 202; data: { run_id: number; status: string } }
    | { status: 409; data: { run_id: number; locked_by: string; lock_expires_at: number } }
  >
}

export const fetchDiagnosticsRefreshRun = (runId: number) =>
  get<DiagnosticRefreshRun>(`/api/diagnostics/refresh/${runId}`)
