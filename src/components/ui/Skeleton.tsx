import React from 'react'
import clsx from 'clsx'

interface Props {
  /** Tailwind width class or arbitrary value, e.g. 'w-full', 'w-24'. Default 'w-full'. */
  width?: string
  /** Tailwind height class, e.g. 'h-4'. Default 'h-4'. */
  height?: string
  /** Render as a circle (e.g. avatar / dot placeholder). */
  circle?: boolean
  /** Extra classes (margins, rounding overrides, etc.). */
  className?: string
}

/** Animated loading placeholder block matching the terminal theme. */
export default function Skeleton({ width = 'w-full', height = 'h-4', circle, className }: Props) {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Loading"
      className={clsx(
        'animate-pulse bg-terminal-muted',
        circle ? 'rounded-full' : 'rounded',
        width,
        height,
        className,
      )}
    />
  )
}
