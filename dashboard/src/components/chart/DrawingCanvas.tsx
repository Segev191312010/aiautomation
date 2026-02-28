/**
 * DrawingCanvas — HTML5 Canvas overlay for chart drawings.
 *
 * Renders drawings (horizontal lines, trendlines, Fibonacci) on top of
 * the lightweight-charts chart via an absolutely positioned <canvas>.
 *
 * Interaction model:
 *   - pointer-events: none by default (chart pan/zoom works underneath)
 *   - pointer-events: auto only when a tool is active or dragging a handle
 *   - Selection + context menu via chart subscriptions when pointer-events off
 */
import { useEffect, useRef, useCallback, useState } from 'react'
import type { IChartApi, ISeriesApi, MouseEventParams } from 'lightweight-charts'
import { useDrawingStore } from '@/store'
import { useMarketStore } from '@/store'
import {
  renderAllDrawings,
  renderPreview,
  hitTestDrawings,
  snapToCandle,
  DRAWING_COLORS,
  type RenderContext,
} from '@/utils/drawingEngine'
import type { Drawing, DrawingPoint, DrawingType } from '@/types/drawing'
import type { OHLCVBar } from '@/types'

// ── Types ──────────────────────────────────────────────────────────────────

interface Props {
  chart: IChartApi
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  series: ISeriesApi<any>
  symbol: string
  timeframe: string
}

type Phase = 'idle' | 'placing_first' | 'placing_second' | 'dragging'

interface InteractionState {
  phase: Phase
  drawingType: DrawingType | null
  anchor: { x: number; y: number; price: number; time: number } | null
  cursor: { x: number; y: number; price: number; time: number } | null
  hoveredDrawingId: string | null
  dragDrawingId: string | null
  dragHandleIndex: number | null
  dragStartPoint: DrawingPoint | null
}

interface ContextMenuState {
  visible: boolean
  x: number
  y: number
  drawingId: string | null
}

// ── Component ──────────────────────────────────────────────────────────────

export default function DrawingCanvas({ chart, series, symbol, timeframe }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const stateRef = useRef<InteractionState>({
    phase: 'idle',
    drawingType: null,
    anchor: null,
    cursor: null,
    hoveredDrawingId: null,
    dragDrawingId: null,
    dragHandleIndex: null,
    dragStartPoint: null,
  })

  // Label editing
  const [editingLabel, setEditingLabel] = useState<{
    drawingId: string; x: number; y: number; value: string
  } | null>(null)

  // Context menu
  const [ctxMenu, setCtxMenu] = useState<ContextMenuState>({
    visible: false, x: 0, y: 0, drawingId: null,
  })

  // Crosshair position (for paste)
  const crosshairRef = useRef<{ x: number; y: number; price: number; time: number } | null>(null)

  // Drawing key for this symbol + timeframe
  const drawingKey = `${symbol}_${timeframe}`

  // ── Store selectors ────────────────────────────────────────────────────

  const drawings = useDrawingStore((s) => s.drawings[drawingKey] ?? [])
  const activeTool = useDrawingStore((s) => s.activeTool)
  const selectedDrawingId = useDrawingStore((s) => s.selectedDrawingId)
  const drawingColor = useDrawingStore((s) => s.drawingColor)

  const {
    addDrawing, updateDrawing, removeDrawing, setActiveTool,
    setSelectedDrawing, toggleLock, undo, redo,
    copySelected, paste, _flushSave,
  } = useDrawingStore()

  const bars = useMarketStore((s) => s.bars[symbol] ?? []) as OHLCVBar[]

  // ── Coordinate converters ──────────────────────────────────────────────

  const priceToY = useCallback((price: number): number | null => {
    try { return (series as ISeriesApi<'Candlestick'>).priceToCoordinate(price) as number | null } catch { return null }
  }, [series])

  const timeToX = useCallback((time: number): number | null => {
    try { return chart.timeScale().timeToCoordinate(time as never) as number | null } catch { return null }
  }, [chart])

  const yToPrice = useCallback((y: number): number | null => {
    try { return (series as ISeriesApi<'Candlestick'>).coordinateToPrice(y) as number | null } catch { return null }
  }, [series])

  const xToTime = useCallback((x: number): number | null => {
    try { return chart.timeScale().coordinateToTime(x) as number | null } catch { return null }
  }, [chart])

  // ── Interval to seconds helper ─────────────────────────────────────────

  const barSecondsRef = useRef(86400)
  useEffect(() => {
    // Estimate bar seconds from first two bars
    if (bars.length >= 2) {
      barSecondsRef.current = Math.abs(bars[1].time - bars[0].time) || 86400
    }
  }, [bars])

  // ── Redraw ─────────────────────────────────────────────────────────────

  const redraw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const w = canvas.clientWidth
    const h = canvas.clientHeight

    // Sync canvas buffer size to display size
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr
      canvas.height = h * dpr
      ctx.scale(dpr, dpr)
    }

    const rc: RenderContext = {
      priceToY,
      timeToX,
      canvasWidth: w,
      canvasHeight: h,
      selectedId: selectedDrawingId,
      hoveredId: stateRef.current.hoveredDrawingId,
      barSeconds: barSecondsRef.current,
    }

    renderAllDrawings(ctx, drawings, rc)

    // Render in-progress preview
    const st = stateRef.current
    if (st.phase === 'placing_first' && st.cursor && activeTool === 'horizontal_line') {
      renderPreview(ctx, 'horizontal_line', drawingColor, st.cursor, st.cursor, rc)
    } else if (st.phase === 'placing_second' && st.anchor && st.cursor && st.drawingType) {
      renderPreview(ctx, st.drawingType, drawingColor, st.anchor, st.cursor, rc)
    }
  }, [drawings, selectedDrawingId, activeTool, drawingColor, priceToY, timeToX])

  // ── Canvas sizing via ResizeObserver ────────────────────────────────────

  useEffect(() => {
    const wrapper = wrapperRef.current
    if (!wrapper) return
    const observer = new ResizeObserver(() => redraw())
    observer.observe(wrapper)
    return () => observer.disconnect()
  }, [redraw])

  // ── Chart viewport change subscription ─────────────────────────────────

  useEffect(() => {
    const handler = () => redraw()
    chart.timeScale().subscribeVisibleLogicalRangeChange(handler)
    return () => chart.timeScale().unsubscribeVisibleLogicalRangeChange(handler)
  }, [chart, redraw])

  // ── Redraw on drawing/selection changes ────────────────────────────────

  useEffect(() => { redraw() }, [redraw])

  // ── Crosshair move — hover detection + crosshair position storage ─────

  useEffect(() => {
    const handler = (param: MouseEventParams) => {
      if (!param.point) return
      const { x, y } = param.point

      // Store crosshair position for paste
      const price = yToPrice(y)
      const time = xToTime(x)
      if (price !== null && time !== null) {
        crosshairRef.current = { x, y, price, time: time as number }
      }

      // Hit-test for hover (only when not placing)
      const st = stateRef.current
      if (st.phase === 'idle' || st.phase === 'placing_first') {
        const hit = hitTestDrawings(x, y, drawings, priceToY, timeToX, canvasRef.current?.clientWidth ?? 800)
        const newHoveredId = hit?.drawingId ?? null
        if (newHoveredId !== st.hoveredDrawingId) {
          st.hoveredDrawingId = newHoveredId
          redraw()
        }
      }

      // Update cursor for preview during placement
      if (st.phase === 'placing_first' || st.phase === 'placing_second') {
        if (price !== null && time !== null) {
          st.cursor = { x, y, price, time: time as number }
          redraw()
        }
      }
    }

    chart.subscribeCrosshairMove(handler)
    return () => chart.unsubscribeCrosshairMove(handler)
  }, [chart, drawings, priceToY, timeToX, yToPrice, xToTime, redraw])

  // ── Chart click subscription — selection when pointer-events off ───────

  useEffect(() => {
    const handler = (param: MouseEventParams) => {
      if (!param.point) return
      // Only handle clicks when no tool is active (pointer-events: none on canvas)
      if (activeTool) return

      const { x, y } = param.point
      const hit = hitTestDrawings(x, y, drawings, priceToY, timeToX, canvasRef.current?.clientWidth ?? 800)

      if (hit) {
        setSelectedDrawing(hit.drawingId)
      } else {
        setSelectedDrawing(null)
      }
      redraw()
    }

    chart.subscribeClick(handler)
    return () => chart.unsubscribeClick(handler)
  }, [chart, activeTool, drawings, priceToY, timeToX, redraw, setSelectedDrawing])

  // ── Mouse handlers for canvas (when pointer-events: auto) ──────────────

  const getMouseCoords = useCallback((e: React.MouseEvent) => {
    const rect = canvasRef.current?.getBoundingClientRect()
    if (!rect) return null
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    const price = yToPrice(y)
    const time = xToTime(x)
    if (price === null) return null
    return { x, y, price, time: time as number | null }
  }, [yToPrice, xToTime])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0) return // left click only
    const coords = getMouseCoords(e)
    if (!coords || coords.time === null) return

    const st = stateRef.current
    const shiftKey = e.shiftKey

    // Snap-to-candle with Shift
    let finalPrice = coords.price
    let finalTime = coords.time
    if (shiftKey && bars.length > 0) {
      const snapped = snapToCandle(coords.price, coords.time, bars)
      finalPrice = snapped.price
      finalTime = snapped.time
    }

    if (st.phase === 'idle' && activeTool) {
      // Start placement
      if (activeTool === 'horizontal_line') {
        // H-line completes immediately
        const drawing: Drawing = {
          id: crypto.randomUUID(),
          type: 'horizontal_line',
          symbol,
          timeframe,
          color: drawingColor,
          points: [{ time: finalTime, price: finalPrice }],
          visible: true,
          locked: false,
        }
        addDrawing(drawing)
        setActiveTool(null)
      } else {
        // Trendline / Fibonacci — need second point
        st.phase = 'placing_second'
        st.drawingType = activeTool
        st.anchor = { x: coords.x, y: coords.y, price: finalPrice, time: finalTime }
        st.cursor = st.anchor
      }
    } else if (st.phase === 'placing_second' && st.anchor && st.drawingType) {
      // Complete two-point drawing
      const drawing: Drawing = {
        id: crypto.randomUUID(),
        type: st.drawingType,
        symbol,
        timeframe,
        color: drawingColor,
        points: [
          { time: st.anchor.time, price: st.anchor.price },
          { time: finalTime, price: finalPrice },
        ],
        visible: true,
        locked: false,
      }
      addDrawing(drawing)
      setActiveTool(null)
      st.phase = 'idle'
      st.anchor = null
      st.cursor = null
      st.drawingType = null
    } else if (!activeTool) {
      // Check for handle drag (when selected)
      const hit = hitTestDrawings(
        coords.x, coords.y, drawings, priceToY, timeToX,
        canvasRef.current?.clientWidth ?? 800,
      )
      if (hit && hit.part === 'handle' && hit.handleIndex !== undefined) {
        const drawing = drawings.find((d) => d.id === hit.drawingId)
        if (drawing && !drawing.locked) {
          st.phase = 'dragging'
          st.dragDrawingId = hit.drawingId
          st.dragHandleIndex = hit.handleIndex
          st.dragStartPoint = { ...drawing.points[hit.handleIndex] }
        }
      }
    }
    redraw()
  }, [activeTool, drawingColor, symbol, timeframe, bars, drawings,
    addDrawing, setActiveTool, getMouseCoords, priceToY, timeToX, redraw])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const coords = getMouseCoords(e)
    if (!coords) return

    const st = stateRef.current
    const shiftKey = e.shiftKey

    let finalPrice = coords.price
    let finalTime = coords.time ?? 0
    if (shiftKey && bars.length > 0 && coords.time !== null) {
      const snapped = snapToCandle(coords.price, coords.time, bars)
      finalPrice = snapped.price
      finalTime = snapped.time
    }

    if (st.phase === 'placing_first' || st.phase === 'placing_second') {
      st.cursor = { x: coords.x, y: coords.y, price: finalPrice, time: finalTime }
      redraw()
    } else if (st.phase === 'dragging' && st.dragDrawingId !== null && st.dragHandleIndex !== null) {
      if (coords.time === null) return
      // Update the dragged handle's point
      const drawing = drawings.find((d) => d.id === st.dragDrawingId)
      if (!drawing || drawing.locked) return

      const newPoints = [...drawing.points]
      newPoints[st.dragHandleIndex] = { time: finalTime, price: finalPrice }
      updateDrawing(st.dragDrawingId, { points: newPoints })
    }
  }, [getMouseCoords, bars, drawings, updateDrawing, redraw])

  const handleMouseUp = useCallback(() => {
    const st = stateRef.current
    if (st.phase === 'dragging') {
      st.phase = 'idle'
      st.dragDrawingId = null
      st.dragHandleIndex = null
      st.dragStartPoint = null
    }
  }, [])

  // ── Context menu ───────────────────────────────────────────────────────

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const coords = getMouseCoords(e)
    if (!coords) return

    const hit = hitTestDrawings(
      coords.x, coords.y, drawings, priceToY, timeToX,
      canvasRef.current?.clientWidth ?? 800,
    )

    if (hit) {
      setCtxMenu({ visible: true, x: e.clientX, y: e.clientY, drawingId: hit.drawingId })
      setSelectedDrawing(hit.drawingId)
    } else {
      setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null })
    }
  }, [getMouseCoords, drawings, priceToY, timeToX, setSelectedDrawing])

  // Also handle right-click via chart container when pointer-events is none
  useEffect(() => {
    const wrapper = wrapperRef.current
    if (!wrapper) return

    const handler = (e: MouseEvent) => {
      if (activeTool) return // canvas handles it
      const rect = wrapper.getBoundingClientRect()
      const x = e.clientX - rect.left
      const y = e.clientY - rect.top

      const hit = hitTestDrawings(x, y, drawings, priceToY, timeToX, wrapper.clientWidth)
      if (hit) {
        e.preventDefault()
        setCtxMenu({ visible: true, x: e.clientX, y: e.clientY, drawingId: hit.drawingId })
        setSelectedDrawing(hit.drawingId)
      }
    }

    wrapper.addEventListener('contextmenu', handler)
    return () => wrapper.removeEventListener('contextmenu', handler)
  }, [activeTool, drawings, priceToY, timeToX, setSelectedDrawing])

  // Close context menu on click outside
  useEffect(() => {
    if (!ctxMenu.visible) return
    const handler = () => setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null })
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [ctxMenu.visible])

  // ── Double-click for label editing ─────────────────────────────────────

  const handleDoubleClick = useCallback((e: React.MouseEvent) => {
    const coords = getMouseCoords(e)
    if (!coords) return

    const hit = hitTestDrawings(
      coords.x, coords.y, drawings, priceToY, timeToX,
      canvasRef.current?.clientWidth ?? 800,
    )
    if (hit && hit.part === 'label') {
      const drawing = drawings.find((d) => d.id === hit.drawingId)
      if (drawing) {
        const rect = canvasRef.current?.getBoundingClientRect()
        if (rect) {
          setEditingLabel({
            drawingId: hit.drawingId,
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
            value: drawing.label || drawing.points[0]?.price.toFixed(2) || '',
          })
        }
      }
    }
  }, [getMouseCoords, drawings, priceToY, timeToX])

  // ── Keyboard shortcuts ─────────────────────────────────────────────────

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      // Skip if editing label or in an input
      if (editingLabel) return
      const target = e.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return

      const st = stateRef.current
      const ctrl = e.ctrlKey || e.metaKey

      switch (e.key) {
        case 'Escape':
          if (st.phase !== 'idle') {
            // Cancel placement
            st.phase = 'idle'
            st.anchor = null
            st.cursor = null
            st.drawingType = null
            setActiveTool(null)
            redraw()
          } else if (selectedDrawingId) {
            setSelectedDrawing(null)
            redraw()
          } else if (activeTool) {
            setActiveTool(null)
          }
          e.preventDefault()
          break

        case 'Delete':
        case 'Backspace':
          if (selectedDrawingId) {
            const d = drawings.find((dr) => dr.id === selectedDrawingId)
            if (d && !d.locked) {
              removeDrawing(selectedDrawingId)
              redraw()
            }
          }
          e.preventDefault()
          break

        case 'h':
        case 'H':
          if (!ctrl) { setActiveTool(activeTool === 'horizontal_line' ? null : 'horizontal_line'); e.preventDefault() }
          break
        case 't':
        case 'T':
          if (!ctrl) { setActiveTool(activeTool === 'trendline' ? null : 'trendline'); e.preventDefault() }
          break
        case 'f':
        case 'F':
          if (!ctrl) { setActiveTool(activeTool === 'fibonacci' ? null : 'fibonacci'); e.preventDefault() }
          break

        case 'z':
        case 'Z':
          if (ctrl && e.shiftKey) { redo(); e.preventDefault() }
          else if (ctrl) { undo(); e.preventDefault() }
          break

        case 'c':
        case 'C':
          if (ctrl) { copySelected(); e.preventDefault() }
          break
        case 'v':
        case 'V':
          if (ctrl && crosshairRef.current) {
            paste(drawingKey, crosshairRef.current.price, crosshairRef.current.time)
            e.preventDefault()
          }
          break

        case 'ArrowUp':
        case 'ArrowDown':
        case 'ArrowLeft':
        case 'ArrowRight': {
          if (!selectedDrawingId) break
          const d = drawings.find((dr) => dr.id === selectedDrawingId)
          if (!d || d.locked) break
          const nudge = e.shiftKey ? 10 : 1
          // Compute price/time nudge from pixel nudge
          const pUp = yToPrice(0)
          const pDown = yToPrice(nudge)
          const pricePerPx = (pUp !== null && pDown !== null) ? Math.abs(pUp - pDown) : 0
          const tLeft = xToTime(0)
          const tRight = xToTime(nudge)
          const timePerPx = (tLeft !== null && tRight !== null) ? Math.abs((tRight as number) - (tLeft as number)) : 0

          let dp = 0, dt = 0
          if (e.key === 'ArrowUp') dp = pricePerPx
          if (e.key === 'ArrowDown') dp = -pricePerPx
          if (e.key === 'ArrowRight') dt = timePerPx
          if (e.key === 'ArrowLeft') dt = -timePerPx

          const newPoints = d.points.map((p) => ({
            time: p.time + dt,
            price: p.price + dp,
          }))
          updateDrawing(d.id, { points: newPoints })
          e.preventDefault()
          break
        }
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [
    activeTool, selectedDrawingId, drawings, editingLabel, drawingKey,
    setActiveTool, setSelectedDrawing, removeDrawing, updateDrawing,
    undo, redo, copySelected, paste, yToPrice, xToTime, redraw,
  ])

  // ── Cancel placement on symbol/timeframe change ────────────────────────

  useEffect(() => {
    const st = stateRef.current
    if (st.phase !== 'idle') {
      st.phase = 'idle'
      st.anchor = null
      st.cursor = null
      st.drawingType = null
    }
  }, [symbol, timeframe])

  // ── Activate placement mode when tool selected ─────────────────────────

  useEffect(() => {
    const st = stateRef.current
    if (activeTool) {
      st.phase = 'placing_first'
      st.drawingType = activeTool
    } else {
      if (st.phase === 'placing_first' || st.phase === 'placing_second') {
        st.phase = 'idle'
        st.anchor = null
        st.cursor = null
        st.drawingType = null
      }
    }
    redraw()
  }, [activeTool, redraw])

  // ── beforeunload + visibilitychange for persistence ────────────────────

  useEffect(() => {
    const onBeforeUnload = () => _flushSave()
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') _flushSave()
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [_flushSave])

  // ── Pointer events policy ──────────────────────────────────────────────

  const pointerEvents = activeTool || stateRef.current.phase === 'dragging' ? 'auto' : 'none'
  const cursor = activeTool ? 'crosshair' : stateRef.current.hoveredDrawingId ? 'pointer' : 'default'

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div ref={wrapperRef} className="absolute inset-0" style={{ pointerEvents: 'none' }}>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full"
        style={{ pointerEvents, cursor }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onContextMenu={handleContextMenu}
        onDoubleClick={handleDoubleClick}
      />

      {/* Context Menu */}
      {ctxMenu.visible && ctxMenu.drawingId && (
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          drawingId={ctxMenu.drawingId}
          drawings={drawings}
          onClose={() => setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null })}
          onDelete={(id) => { removeDrawing(id); setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null }) }}
          onToggleLock={(id) => { toggleLock(id); setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null }) }}
          onToggleExtend={(id, dir) => {
            const d = drawings.find((dr) => dr.id === id)
            if (d) {
              const opts = d.options || {}
              if (dir === 'left') updateDrawing(id, { options: { ...opts, extendLeft: !opts.extendLeft } })
              else updateDrawing(id, { options: { ...opts, extendRight: !opts.extendRight } })
            }
            setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null })
          }}
          onChangeColor={(id, color) => {
            updateDrawing(id, { color })
            setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null })
          }}
          onEditLabel={(id) => {
            const d = drawings.find((dr) => dr.id === id)
            if (d) {
              setEditingLabel({
                drawingId: id,
                x: ctxMenu.x,
                y: ctxMenu.y,
                value: d.label || '',
              })
            }
            setCtxMenu({ visible: false, x: 0, y: 0, drawingId: null })
          }}
        />
      )}

      {/* Label editing overlay */}
      {editingLabel && (
        <LabelEditor
          value={editingLabel.value}
          x={editingLabel.x}
          y={editingLabel.y}
          onCommit={(text) => {
            updateDrawing(editingLabel.drawingId, { label: text || undefined })
            setEditingLabel(null)
          }}
          onCancel={() => setEditingLabel(null)}
        />
      )}
    </div>
  )
}

// ── Context Menu Sub-component ───────────────────────────────────────────

function ContextMenu({
  x, y, drawingId, drawings, onClose, onDelete, onToggleLock,
  onToggleExtend, onChangeColor, onEditLabel,
}: {
  x: number; y: number; drawingId: string; drawings: Drawing[]
  onClose: () => void
  onDelete: (id: string) => void
  onToggleLock: (id: string) => void
  onToggleExtend: (id: string, dir: 'left' | 'right') => void
  onChangeColor: (id: string, color: string) => void
  onEditLabel: (id: string) => void
}) {
  const drawing = drawings.find((d) => d.id === drawingId)
  if (!drawing) return null

  const menuStyle: React.CSSProperties = {
    position: 'fixed',
    left: x,
    top: y,
    zIndex: 999,
    pointerEvents: 'auto',
  }

  return (
    <div style={menuStyle} className="bg-terminal-elevated border border-terminal-border rounded-lg shadow-terminal py-1 min-w-[160px]">
      <button
        onClick={() => onEditLabel(drawingId)}
        className="w-full text-left px-3 py-1.5 text-[11px] font-mono text-terminal-dim hover:text-terminal-text hover:bg-terminal-muted/30 transition-colors"
      >
        Edit Label
      </button>
      <button
        onClick={() => onToggleLock(drawingId)}
        className="w-full text-left px-3 py-1.5 text-[11px] font-mono text-terminal-dim hover:text-terminal-text hover:bg-terminal-muted/30 transition-colors"
      >
        {drawing.locked ? 'Unlock' : 'Lock'}
      </button>
      {drawing.type === 'trendline' && (
        <>
          <button
            onClick={() => onToggleExtend(drawingId, 'left')}
            className="w-full text-left px-3 py-1.5 text-[11px] font-mono text-terminal-dim hover:text-terminal-text hover:bg-terminal-muted/30 transition-colors"
          >
            {drawing.options?.extendLeft ? 'Unextend Left' : 'Extend Left'}
          </button>
          <button
            onClick={() => onToggleExtend(drawingId, 'right')}
            className="w-full text-left px-3 py-1.5 text-[11px] font-mono text-terminal-dim hover:text-terminal-text hover:bg-terminal-muted/30 transition-colors"
          >
            {drawing.options?.extendRight ? 'Unextend Right' : 'Extend Right'}
          </button>
        </>
      )}
      <div className="border-t border-terminal-border my-1" />
      <div className="px-3 py-1.5 flex gap-1">
        {DRAWING_COLORS.map((c) => (
          <button
            key={c}
            onClick={() => onChangeColor(drawingId, c)}
            className="w-3.5 h-3.5 rounded-full border border-terminal-border hover:scale-125 transition-transform"
            style={{ backgroundColor: c, borderColor: c === drawing.color ? '#dce8f5' : undefined }}
          />
        ))}
      </div>
      <div className="border-t border-terminal-border my-1" />
      <button
        onClick={() => onDelete(drawingId)}
        className="w-full text-left px-3 py-1.5 text-[11px] font-mono text-terminal-red hover:bg-terminal-red/10 transition-colors"
      >
        Delete
      </button>
    </div>
  )
}

// ── Label Editor Sub-component ───────────────────────────────────────────

function LabelEditor({
  value, x, y, onCommit, onCancel,
}: {
  value: string; x: number; y: number
  onCommit: (text: string) => void
  onCancel: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  return (
    <input
      ref={inputRef}
      defaultValue={value}
      className="absolute bg-terminal-elevated border border-terminal-blue text-terminal-text text-[11px] font-mono px-2 py-1 rounded shadow-terminal outline-none"
      style={{ left: x - 60, top: y - 12, width: 120, zIndex: 1000, pointerEvents: 'auto' }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onCommit((e.target as HTMLInputElement).value)
        if (e.key === 'Escape') onCancel()
        e.stopPropagation()
      }}
      onBlur={(e) => onCommit(e.target.value)}
    />
  )
}
