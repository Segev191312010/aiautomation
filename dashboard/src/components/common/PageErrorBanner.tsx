interface PageErrorBannerProps {
  message?: string
  show: boolean
}

export default function PageErrorBanner({ message, show }: PageErrorBannerProps) {
  if (!show) return null

  return (
    <div
      role="alert"
      aria-live="polite"
      className="mb-4 rounded-xl border border-theme-warning/30 bg-theme-warning/10 px-4 py-2.5 flex items-center gap-2"
    >
      <span className="inline-flex h-2 w-2 rounded-full bg-theme-warning shrink-0" />
      <p className="text-xs font-sans text-theme-warning">
        {message ?? 'Some data sections are temporarily unavailable.'}
      </p>
    </div>
  )
}
