import type {
  EnrichResult,
  IBKRScanResponse,
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
