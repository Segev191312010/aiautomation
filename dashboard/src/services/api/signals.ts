import type { ClaudeDecision, SignalRow, SignalSource, SignalStatus } from '@/types/signals'
import { get } from './client'

export interface FetchSignalsParams {
  source?: SignalSource
  status?: SignalStatus
  limit?: number
  offset?: number
}

export const fetchSignals = (params: FetchSignalsParams = {}) => {
  const query = new URLSearchParams()
  if (params.source) query.set('source', params.source)
  if (params.status) query.set('status', params.status)
  if (params.limit != null) query.set('limit', String(params.limit))
  if (params.offset != null) query.set('offset', String(params.offset))
  const qs = query.toString()
  return get<SignalRow[]>(`/api/signals${qs ? `?${qs}` : ''}`)
}

export const fetchDecisionForSignal = (id: string) =>
  get<ClaudeDecision | null>(`/api/signals/${id}/decision`)
