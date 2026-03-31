import clsx from 'clsx'

interface SectionSkeletonProps {
  lines?: number
  className?: string
}

export default function SectionSkeleton({ lines = 3, className }: SectionSkeletonProps) {
  return (
    <div className={clsx('animate-pulse space-y-3 rounded-2xl border border-zinc-800 bg-zinc-900/50 p-5', className)}>
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className="h-4 rounded bg-zinc-800"
          style={{ width: `${70 + Math.random() * 30}%` }}
        />
      ))}
    </div>
  )
}
