/**
 * Shared async state vocabulary — used by domain hooks and async state components.
 */

export type AsyncStatus = 'idle' | 'loading' | 'success' | 'error' | 'degraded'

export interface AsyncState<T> {
  data: T | null
  status: AsyncStatus
  error: string | null
}

export type SectionStatus = 'loading' | 'loaded' | 'unavailable'
