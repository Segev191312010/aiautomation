/**
 * ResizeHandle — a draggable divider between chart panes.
 *
 * Fires onDelta(dy) during drag (rAF-throttled with accumulated deltas)
 * so the parent can adjust pane heights. Double-click resets to default.
 */
import { useRef, useCallback } from 'react'

interface Props {
  onDelta:       (dy: number) => void
  onDoubleClick?: () => void
}

export default function ResizeHandle({ onDelta, onDoubleClick }: Props) {
  const draggingRef  = useRef(false)
  const lastYRef     = useRef(0)
  const rafRef       = useRef(0)
  const pendingDyRef = useRef(0)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    draggingRef.current = true
    lastYRef.current = e.clientY
    pendingDyRef.current = 0

    const onMouseMove = (ev: MouseEvent) => {
      if (!draggingRef.current) return
      const dy = ev.clientY - lastYRef.current
      lastYRef.current = ev.clientY

      // Accumulate deltas so none are lost between frames
      pendingDyRef.current += dy

      if (!rafRef.current) {
        rafRef.current = requestAnimationFrame(() => {
          const accumulated = pendingDyRef.current
          pendingDyRef.current = 0
          rafRef.current = 0
          onDelta(accumulated)
        })
      }
    }

    const onMouseUp = () => {
      draggingRef.current = false
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = 0
      }
      // Flush any remaining delta
      if (pendingDyRef.current !== 0) {
        onDelta(pendingDyRef.current)
        pendingDyRef.current = 0
      }
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
  }, [onDelta])

  return (
    <div
      className="h-1.5 shrink-0 cursor-row-resize flex items-center justify-center group hover:bg-indigo-600/10 transition-colors rounded"
      onMouseDown={onMouseDown}
      onDoubleClick={onDoubleClick}
    >
      <div className="w-8 h-px bg-gray-200 group-hover:bg-indigo-600/40 transition-colors" />
    </div>
  )
}
