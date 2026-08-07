import type {
  EnrichResult,
  IBKRScanResponse,
  IBKRScanResult,
  IBKRScanTemplate,
  ScanFilter,
  ScanResponse,
  ScreenerPreset,
  UniverseInfo,
} from '@/types'
import { get, post, del } from './client'

export const runScan = (request: {
  universe: string
  symbols?: string[]
  filters: ScanFilter[]
  interval: string
  period: string
  limit: number
}) => post<ScanResponse>('/api/screener/scan', request)

export const fetchUniverses = () => get<UniverseInfo[]>('/api/screener/universes')

export const fetchScreenerPresets = () => get<ScreenerPreset[]>('/api/screener/presets')

export const saveScreenerPreset = (name: string, filters: ScanFilter[]) =>
  post<ScreenerPreset>('/api/screener/presets', { name, filters })

export const deleteScreenerPreset = (id: string) =>
  del<{ deleted: boolean }>(`/api/screener/presets/${id}`)

export const enrichSymbols = (symbols: string[]) =>
  post<EnrichResult[]>('/api/screener/enrich', { symbols })

export const fetchIBKRScans = () =>
  get<IBKRScanTemplate[]>('/api/screener/ibkr-scans')

export const runIBKRScan = (scanName: string, maxResults: number = 50) =>
  get<IBKRScanResponse>(`/api/screener/ibkr-scan/${scanName}?max_results=${maxResults}`)

export const runIBKRMultiScan = (scans?: string[]) =>
  post<Record<string, { results: IBKRScanResult[]; count: number }>>(
    '/api/screener/ibkr-multi-scan',
    { scans: scans ?? null },
  )

// ── Unified Screener Pipeline (Phase 2) ────────────────────────────────────

export interface ScreenerPipelineStatus {
  connected: boolean
  last_scan_at: string | null
  last_scan_source: string
  last_scan_duration_ms: number
  candidate_count: number
  top_symbols: string[]
  data_age_seconds: number
  stale: boolean
  errors: string[]
}

export interface ScreenerPipelineSnapshot {
  scan_id: string
  source: string
  scan_name: string
  candidates: ScreenerPipelineCandidate[]
  total_symbols: number
  skipped_symbols: string[]
  elapsed_ms: number
  created_at: string
  stale_at: string
  errors: string[]
}

export interface ScreenerPipelineCandidate {
  symbol: string
  name: string
  exchange: string
  con_id: number
  price: number
  change_pct: number
  volume: number
  rank: number
  source: 'ibkr' | 'yfinance' | 'fallback'
  sector: string | null
  market_cap: number | null
  indicators: Record<string, number>
  screener_score: number
  setup: string
  relative_volume: number
  momentum_20d: number
  trend_strength: number
  notes: string[]
}

export const fetchPipelineStatus = () =>
  get<ScreenerPipelineStatus>('/api/screener/pipeline/status')

export const fetchPipelineSnapshot = () =>
  get<ScreenerPipelineSnapshot>('/api/screener/pipeline/snapshot')

export const triggerPipelineScan = () =>
  post<ScreenerPipelineSnapshot>('/api/screener/pipeline/scan-now')
