import clsx from 'clsx'

export default function FreshnessTag({ fetchedAt }: { fetchedAt: number }) {
  const agoSec = Math.max(0, Math.floor(Date.now() / 1000 - fetchedAt))
  const agoMin = Math.floor(agoSec / 60)

  const ageLabel =
    agoMin < 1 ? 'just now' :
    agoMin < 60 ? `${agoMin}m ago` :
    `${Math.floor(agoMin / 60)}h ago`

  const status = agoMin < 5 ? 'live' : agoMin < 60 ? 'cached' : 'stale'

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[9px] font-sans',
        status === 'live' && 'bg-green-50 text-emerald-400 border-green-200',
        status === 'cached' && 'bg-amber-50 text-amber-600 border-amber-200',
        status === 'stale' && 'bg-red-50 text-red-600 border-red-200',
      )}
    >
      <span
        className={clsx(
          'w-1.5 h-1.5 rounded-full shrink-0',
          status === 'live' && 'bg-emerald-600',
          status === 'cached' && 'bg-amber-600',
          status === 'stale' && 'bg-red-600',
        )}
      />
      {status} - {ageLabel}
    </span>
  )
}
