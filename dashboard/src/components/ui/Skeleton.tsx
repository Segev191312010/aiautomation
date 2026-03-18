import React from 'react'

// ── Base Skeleton ─────────────────────────────────────────────────────────────
// Uses a gradient-sweep shimmer rather than a simple opacity pulse.
// The shimmer keyframe is defined in tailwind.config.ts (backgroundPosition sweep).
// We apply it inline so that background-size is set correctly for the sweep.

interface SkeletonProps {
  className?: string
  width?: string
  height?: string
  style?: React.CSSProperties
}

export default function Skeleton({ className = '', width, height, style }: SkeletonProps) {
  return (
    <div
      className={`rounded-xl animate-shimmer ${className}`}
      style={{
        width,
        height,
        // Light base + moving highlight stripe for cream theme
        background: [
          'linear-gradient(',
          '  90deg,',
          '  rgba(0,0,0,0.04) 0%,',
          '  rgba(0,0,0,0.07) 40%,',
          '  rgba(0,0,0,0.04) 80%',
          ')',
        ].join(''),
        backgroundSize: '200% 100%',
        // Allow callers to override individual props (e.g. width via style)
        ...style,
      }}
    />
  )
}

// ── SkeletonCard ──────────────────────────────────────────────────────────────
// Generic metric-card skeleton: label line + value line

export function SkeletonCard({ className = '' }: { className?: string }) {
  return (
    <div className={`card rounded-2xl shadow-card p-4 ${className}`}>
      <Skeleton className="h-3 w-24 mb-3" />
      <Skeleton className="h-6 w-32" />
    </div>
  )
}

// ── SkeletonTable ─────────────────────────────────────────────────────────────
// Generic table skeleton: configurable rows and columns

export function SkeletonTable({ rows = 3, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex gap-4">
          {Array.from({ length: cols }).map((_, j) => (
            <Skeleton key={j} className="h-4 flex-1" />
          ))}
        </div>
      ))}
    </div>
  )
}

// ── SkeletonText ──────────────────────────────────────────────────────────────
// Paragraph of text lines — last line is shorter for realism

export function SkeletonText({ lines = 3, className = '' }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className="h-3"
          // Last line is narrower to simulate paragraph end
          style={{ width: i === lines - 1 ? '65%' : '100%' }}
        />
      ))}
    </div>
  )
}

// ── SkeletonAvatar ────────────────────────────────────────────────────────────
// Circular avatar placeholder

export function SkeletonAvatar({ size = 40, className = '' }: { size?: number; className?: string }) {
  return (
    <Skeleton
      className={`rounded-full shrink-0 ${className}`}
      width={`${size}px`}
      height={`${size}px`}
    />
  )
}

// ── SkeletonChart ─────────────────────────────────────────────────────────────
// Full chart panel placeholder with toolbar + body

export function SkeletonChart({ className = '' }: { className?: string }) {
  return (
    <div className={`card rounded-2xl shadow-card overflow-hidden ${className}`}>
      {/* Toolbar row */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-4 w-10" />
        <Skeleton className="h-4 w-10" />
        <div className="flex-1" />
        <Skeleton className="h-4 w-24" />
      </div>
      {/* Chart body */}
      <Skeleton className="w-full rounded-none" height="240px" />
    </div>
  )
}
