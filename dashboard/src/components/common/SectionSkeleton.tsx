import { cn } from '@/utils/cn'

interface SectionSkeletonProps {
  lines?: number
  className?: string
}

export default function SectionSkeleton({ lines = 3, className }: SectionSkeletonProps) {
  return (
    <div className={cn('animate-pulse space-y-3 rounded-2xl border border-theme-border bg-theme-hover/50 p-5', className)}>
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className="h-4 rounded bg-theme-border"
          style={{ width: `${70 + Math.random() * 30}%` }}
        />
      ))}
    </div>
  )
}
