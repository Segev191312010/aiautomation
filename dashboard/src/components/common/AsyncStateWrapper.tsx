import type { AsyncStatus } from '@/types/async'
import DegradedStateCard from './DegradedStateCard'
import EmptyState from './EmptyState'
import SectionSkeleton from './SectionSkeleton'

interface AsyncStateWrapperProps {
  status: AsyncStatus | 'loaded' | 'unavailable'
  error?: string | null
  isEmpty?: boolean
  emptyTitle?: string
  emptyMessage?: string
  degradedTitle?: string
  degradedReason?: string
  skeletonLines?: number
  children: React.ReactNode
}

export default function AsyncStateWrapper({
  status,
  error,
  isEmpty = false,
  emptyTitle,
  emptyMessage,
  degradedTitle = 'Section unavailable',
  degradedReason,
  skeletonLines = 3,
  children,
}: AsyncStateWrapperProps) {
  if (status === 'loading' || status === 'idle') {
    return <SectionSkeleton lines={skeletonLines} />
  }

  if (status === 'error' || status === 'unavailable') {
    return (
      <DegradedStateCard
        title={degradedTitle}
        reason={degradedReason ?? error ?? 'Unable to load this section.'}
      />
    )
  }

  if (isEmpty) {
    return <EmptyState title={emptyTitle} message={emptyMessage} />
  }

  return <>{children}</>
}
