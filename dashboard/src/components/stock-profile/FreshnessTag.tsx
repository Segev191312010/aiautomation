import clsx from 'clsx'

export default function FreshnessTag({ fetchedAt }: { fetchedAt: number }) {
  const agoS = Math.floor(Date.now() / 1000 - fetchedAt)
  const agoMin = Math.floor(agoS / 60)

  const label =
    agoMin < 1 ? 'Just now' :
    agoMin < 60 ? `${agoMin}m ago` :
    `${Math.floor(agoMin / 60)}h ago`

  return (
    <span
      className={clsx(
        'text-[9px] font-sans',
        agoMin < 15 ? 'text-terminal-green' :
        agoMin < 60 ? 'text-terminal-amber' :
        'text-terminal-red',
      )}
    >
      {label}
    </span>
  )
}
