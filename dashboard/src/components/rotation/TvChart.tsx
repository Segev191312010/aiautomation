function DisabledExternalChart({ label, className }: { label: string; className: string }) {
  return (
    <div className={`${className} flex items-center justify-center rounded-lg border border-[var(--border)] px-5 text-center text-sm text-[var(--text-muted)]`} role="status">
      {label} is unavailable because remote TradingView embeds are disabled by the local-only security policy.
    </div>
  )
}

export function TvChart({ symbol }: { symbol: string }) {
  return <DisabledExternalChart label={`${symbol} external chart`} className="min-h-[400px] h-full w-full" />
}

export function TvMiniChart({ symbol }: { symbol: string }) {
  return <DisabledExternalChart label={`${symbol} external mini chart`} className="h-full min-h-28 w-full" />
}
