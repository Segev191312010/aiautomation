import clsx from 'clsx'

interface EmptyStateProps {
  title?: string
  message?: string
  icon?: React.ReactNode
  compact?: boolean
}

export default function EmptyState({
  title = 'No data',
  message = 'No data available yet.',
  icon,
  compact = false,
}: EmptyStateProps) {
  return (
    <div
      role="status"
      className={clsx(
        'flex flex-col items-center justify-center text-center rounded-2xl border border-zinc-800 bg-zinc-900/50',
        compact ? 'p-4 gap-2' : 'p-8 gap-3',
      )}
    >
      {icon ?? (
        <svg viewBox="0 0 24 24" fill="none" className="w-8 h-8 text-zinc-600" aria-hidden="true">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          <polyline points="7,10 12,15 17,10" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      <div>
        <p className="text-sm font-sans font-medium text-zinc-400">{title}</p>
        <p className="mt-1 text-xs font-sans text-zinc-500">{message}</p>
      </div>
    </div>
  )
}
