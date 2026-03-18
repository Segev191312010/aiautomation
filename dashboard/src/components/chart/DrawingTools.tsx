/**
 * DrawingTools — toolbar for chart drawing tools.
 *
 * Provides: tool selection (H-Line, Trendline, Fibonacci),
 * color picker, delete/clear, auto-save indicator, export/import.
 */
import { useRef, useState } from 'react'
import clsx from 'clsx'
import { useDrawingStore, type SaveStatus } from '@/store'
import { DRAWING_COLORS } from '@/utils/drawingEngine'
import { useToast } from '@/components/ui/ToastProvider'
import type { DrawingType } from '@/types/drawing'

// ── Tool definitions ─────────────────────────────────────────────────────

const TOOLS: Array<{ type: DrawingType; label: string; icon: string; shortcut: string }> = [
  { type: 'horizontal_line', label: 'H-Line',    icon: '─',  shortcut: 'H' },
  { type: 'trendline',       label: 'Trendline', icon: '╱',  shortcut: 'T' },
  { type: 'fibonacci',       label: 'Fib',       icon: '⊟',  shortcut: 'F' },
]

// ── Save status display ──────────────────────────────────────────────────

function SaveIndicator({ status }: { status: SaveStatus }) {
  if (status === 'idle') return null
  return (
    <span className={clsx(
      'text-[10px] font-mono ml-1',
      status === 'saving' && 'text-amber-600 animate-pulse',
      status === 'saved'  && 'text-emerald-400',
      status === 'error'  && 'text-red-400',
    )}>
      {status === 'saving' ? '●' : status === 'saved' ? '✓' : '✗'}
    </span>
  )
}

// ── Component ────────────────────────────────────────────────────────────

interface Props {
  symbol: string
  timeframe: string
  className?: string
}

export default function DrawingTools({ symbol, timeframe, className }: Props) {
  const toast = useToast()
  const importInputRef = useRef<HTMLInputElement>(null)
  const [showColors, setShowColors] = useState(false)

  const drawingKey = `${symbol}_${timeframe}`

  const activeTool        = useDrawingStore((s) => s.activeTool)
  const setActiveTool     = useDrawingStore((s) => s.setActiveTool)
  const drawingColor      = useDrawingStore((s) => s.drawingColor)
  const setDrawingColor   = useDrawingStore((s) => s.setDrawingColor)
  const selectedDrawingId = useDrawingStore((s) => s.selectedDrawingId)
  const removeDrawing     = useDrawingStore((s) => s.removeDrawing)
  const clearDrawings     = useDrawingStore((s) => s.clearDrawings)
  const exportDrawings    = useDrawingStore((s) => s.exportDrawings)
  const importDrawings    = useDrawingStore((s) => s.importDrawings)
  const saveStatus        = useDrawingStore((s) => s.saveStatus)
  const drawings          = useDrawingStore((s) => s.drawings[drawingKey] ?? [])

  // ── Handlers ───────────────────────────────────────────────────────────

  const handleToolClick = (type: DrawingType) => {
    setActiveTool(activeTool === type ? null : type)
  }

  const handleExport = () => {
    const json = exportDrawings()
    // Copy to clipboard
    navigator.clipboard.writeText(json).then(() => {
      toast.info('Drawings exported to clipboard')
    }).catch(() => {
      // Fallback: download as file
      const blob = new Blob([json], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `drawings_${symbol}_${new Date().toISOString().slice(0, 10)}.json`
      a.click()
      URL.revokeObjectURL(url)
      toast.info('Drawings exported as file')
    })
  }

  const handleImport = () => {
    importInputRef.current?.click()
  }

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const result = importDrawings(reader.result as string)
      if (result.ok) {
        toast.info(`Drawings imported${result.errors.length ? ` (${result.errors.length} warnings)` : ''}`)
      } else {
        toast.error(`Import failed: ${result.errors[0] ?? 'unknown error'}`)
      }
    }
    reader.readAsText(file)
    // Reset so same file can be re-imported
    e.target.value = ''
  }

  const handleClearAll = () => {
    if (drawings.length > 0) {
      clearDrawings(drawingKey)
      toast.info(`Cleared ${drawings.length} drawing${drawings.length > 1 ? 's' : ''}`)
    }
  }

  const handleDelete = () => {
    if (selectedDrawingId) {
      removeDrawing(selectedDrawingId)
      toast.info('Drawing deleted')
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div className={clsx('flex items-center gap-2 flex-wrap', className)}>
      {/* Tool buttons */}
      <div className="flex gap-0.5">
        {TOOLS.map((t) => (
          <button
            key={t.type}
            onClick={() => handleToolClick(t.type)}
            title={`${t.label} (${t.shortcut})`}
            className={clsx(
              'text-[11px] font-mono px-2 py-1 rounded border transition-colors',
              activeTool === t.type
                ? 'border-indigo-600/50 text-indigo-600 bg-indigo-600/10'
                : 'border-zinc-800 text-zinc-500 hover:text-zinc-400',
            )}
          >
            <span className="mr-1">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </div>

      {/* Divider */}
      <div className="w-px h-5 bg-zinc-800" />

      {/* Color picker */}
      <div className="relative">
        <button
          onClick={() => setShowColors(!showColors)}
          className="w-5 h-5 rounded border border-zinc-800 hover:border-zinc-700 transition-colors"
          style={{ backgroundColor: drawingColor }}
          title="Drawing color"
        />
        {showColors && (
          <div className="absolute top-7 left-0 z-50 bg-zinc-900 border border-zinc-800 rounded-lg  p-1.5 flex gap-1">
            {DRAWING_COLORS.map((c) => (
              <button
                key={c}
                onClick={() => { setDrawingColor(c); setShowColors(false) }}
                className={clsx(
                  'w-4 h-4 rounded-full border transition-all',
                  drawingColor === c
                    ? 'border-zinc-800 scale-125'
                    : 'border-zinc-800 hover:border-zinc-700',
                )}
                style={{ backgroundColor: c }}
              />
            ))}
          </div>
        )}
      </div>

      {/* Divider */}
      <div className="w-px h-5 bg-zinc-800" />

      {/* Delete / Clear */}
      <button
        onClick={handleDelete}
        disabled={!selectedDrawingId}
        title="Delete selected (Del)"
        className="text-[11px] font-mono px-2 py-1 rounded border border-zinc-800 text-zinc-500 hover:text-red-400 hover:border-red-300/40 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Delete
      </button>
      <button
        onClick={handleClearAll}
        disabled={drawings.length === 0}
        title="Clear all drawings for this symbol"
        className="text-[11px] font-mono px-2 py-1 rounded border border-zinc-800 text-zinc-500 hover:text-red-400 hover:border-red-300/40 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Clear ({drawings.length})
      </button>

      {/* Divider */}
      <div className="w-px h-5 bg-zinc-800" />

      {/* Export / Import */}
      <button
        onClick={handleExport}
        disabled={Object.keys(useDrawingStore.getState().drawings).length === 0}
        title="Export all drawings as JSON"
        className="text-[11px] font-mono px-2 py-1 rounded border border-zinc-800 text-zinc-500 hover:text-zinc-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
      >
        Export
      </button>
      <button
        onClick={handleImport}
        title="Import drawings from JSON"
        className="text-[11px] font-mono px-2 py-1 rounded border border-zinc-800 text-zinc-500 hover:text-zinc-400 transition-colors"
      >
        Import
      </button>
      <input
        ref={importInputRef}
        type="file"
        accept=".json"
        className="hidden"
        onChange={handleImportFile}
      />

      {/* Save indicator */}
      <SaveIndicator status={saveStatus} />
    </div>
  )
}
