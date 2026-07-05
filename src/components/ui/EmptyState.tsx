import React from 'react'
import clsx from 'clsx'

interface Props {
  /** Primary message. */
  message: string
  /** Optional secondary line of detail. */
  detail?: string
  /** Optional leading icon / glyph (emoji, char, or small node). */
  icon?: React.ReactNode
  /** Optional action node, e.g. a button. */
  action?: React.ReactNode
  className?: string
}

/** Centered placeholder shown when a list / panel has no data. */
export default function EmptyState({ message, detail, icon, action, className }: Props) {
  return (
    <div
      className={clsx(
        'flex flex-col items-center justify-center text-center gap-2 py-10 px-4',
        className,
      )}
    >
      {icon != null && (
        <div className="text-2xl text-terminal-ghost leading-none" aria-hidden="true">
          {icon}
        </div>
      )}
      <p className="text-[12px] font-mono text-terminal-dim">{message}</p>
      {detail && <p className="text-[11px] font-mono text-terminal-ghost">{detail}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}
