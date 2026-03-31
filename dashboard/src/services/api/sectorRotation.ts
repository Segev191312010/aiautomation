import type { SectorHeatmapRow, SectorLeadersResponse, SectorRotation } from '@/types'
import { get } from './client'

export const fetchSectorRotation = (lookbackDays = 90): Promise<SectorRotation[]> =>
  get<SectorRotation[]>(`/api/sectors/rotation?lookback_days=${lookbackDays}`)

export const fetchSectorLeaders = (sectorEtf: string, topN = 10, period = '3mo'): Promise<SectorLeadersResponse> =>
  get<SectorLeadersResponse>(`/api/sectors/${sectorEtf}/leaders?top_n=${topN}&period=${period}`)

export const fetchSectorHeatmap = (): Promise<SectorHeatmapRow[]> =>
  get<SectorHeatmapRow[]>('/api/sectors/heatmap')
