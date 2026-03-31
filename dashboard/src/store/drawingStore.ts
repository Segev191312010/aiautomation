import { create } from 'zustand'
import type { Drawing, DrawingType, UserSettings } from '@/types'
import { DEFAULT_DRAWING_COLOR } from '@/utils/drawingEngine'
import { validateDrawingsMap, validateDrawingsExport } from '@/utils/drawingSchema'
import * as api from '@/services/api'

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface DrawingState {
  /** All drawings keyed by `${symbol}_${timeframe}`. */
  drawings:          Record<string, Drawing[]>
  activeTool:        DrawingType | null
  selectedDrawingId: string | null
  drawingColor:      string
  saveStatus:        SaveStatus

  /** Undo / redo stacks (dual-stack approach). */
  _undoStack: Array<Record<string, Drawing[]>>
  _redoStack: Array<Record<string, Drawing[]>>

  /** Clipboard for copy/paste. */
  clipboard: Drawing | null

  /** Internal debounce timer. */
  _saveTimer: ReturnType<typeof setTimeout> | null

  // ── Actions ──────────────────────────────────────────────────────────────
  setActiveTool:    (tool: DrawingType | null) => void
  setSelectedDrawing: (id: string | null) => void
  setDrawingColor:  (color: string) => void

  addDrawing:       (drawing: Drawing) => void
  updateDrawing:    (id: string, updates: Partial<Drawing>) => void
  removeDrawing:    (id: string) => void
  clearDrawings:    (key: string) => void
  loadDrawings:     (drawings: Record<string, Drawing[]>) => void

  toggleLock:       (id: string) => void

  undo:             () => void
  redo:             () => void
  _pushHistory:     () => void

  copySelected:     () => void
  paste:            (key: string, crosshairPrice: number, crosshairTime: number) => void

  exportDrawings:   () => string
  importDrawings:   (json: string) => { ok: boolean; errors: string[] }

  _scheduleSave:    () => void
  _flushSave:       () => void
}

/** Max undo history entries. */
const MAX_HISTORY = 50

export const useDrawingStore = create<DrawingState>((set, get) => ({
  drawings:          {},
  activeTool:        null,
  selectedDrawingId: null,
  drawingColor:      DEFAULT_DRAWING_COLOR,
  saveStatus:        'idle' as SaveStatus,

  _undoStack:    [],
  _redoStack:    [],
  clipboard:     null,
  _saveTimer:    null,

  // ── Tool state ───────────────────────────────────────────────────────────

  setActiveTool: (tool) => set({
    activeTool: tool,
    selectedDrawingId: tool ? null : get().selectedDrawingId,
  }),

  setSelectedDrawing: (id) => set({ selectedDrawingId: id }),

  setDrawingColor: (color) => set({ drawingColor: color }),

  // ── CRUD ─────────────────────────────────────────────────────────────────

  addDrawing: (drawing) => {
    get()._pushHistory()
    set((s) => {
      const key = `${drawing.symbol}_${drawing.timeframe}`
      const existing = s.drawings[key] ?? []
      return { drawings: { ...s.drawings, [key]: [...existing, drawing] } }
    })
    get()._scheduleSave()
  },

  updateDrawing: (id, updates) => {
    get()._pushHistory()
    set((s) => {
      const newDrawings: Record<string, Drawing[]> = {}
      for (const [key, list] of Object.entries(s.drawings)) {
        newDrawings[key] = list.map((d) => d.id === id ? { ...d, ...updates } : d)
      }
      return { drawings: newDrawings }
    })
    get()._scheduleSave()
  },

  removeDrawing: (id) => {
    get()._pushHistory()
    set((s) => {
      const newDrawings: Record<string, Drawing[]> = {}
      for (const [key, list] of Object.entries(s.drawings)) {
        newDrawings[key] = list.filter((d) => d.id !== id)
      }
      return {
        drawings: newDrawings,
        selectedDrawingId: s.selectedDrawingId === id ? null : s.selectedDrawingId,
      }
    })
    get()._scheduleSave()
  },

  clearDrawings: (key) => {
    get()._pushHistory()
    set((s) => ({
      drawings: { ...s.drawings, [key]: [] },
      selectedDrawingId: null,
    }))
    get()._scheduleSave()
  },

  loadDrawings: (drawings) => {
    const { valid } = validateDrawingsMap(drawings)
    set({ drawings: valid, _undoStack: [], _redoStack: [] })
  },

  toggleLock: (id) => {
    set((s) => {
      const newDrawings: Record<string, Drawing[]> = {}
      for (const [key, list] of Object.entries(s.drawings)) {
        newDrawings[key] = list.map((d) => d.id === id ? { ...d, locked: !d.locked } : d)
      }
      return { drawings: newDrawings }
    })
    get()._scheduleSave()
  },

  // ── Undo / Redo (dual-stack) ────────────────────────────────────────────
  //
  // _pushHistory: snapshot current state → undoStack, clear redoStack
  // undo: push current → redoStack, pop undoStack → apply
  // redo: push current → undoStack, pop redoStack → apply

  _pushHistory: () => {
    set((s) => {
      const snapshot = JSON.parse(JSON.stringify(s.drawings)) as Record<string, Drawing[]>
      return {
        _undoStack: [...s._undoStack, snapshot].slice(-MAX_HISTORY),
        _redoStack: [],  // new action clears redo
      }
    })
  },

  undo: () => {
    set((s) => {
      if (s._undoStack.length === 0) return {}
      const prev = s._undoStack[s._undoStack.length - 1]
      const snapshot = JSON.parse(JSON.stringify(s.drawings))
      return {
        drawings: prev,
        _undoStack: s._undoStack.slice(0, -1),
        _redoStack: [...s._redoStack, snapshot],
        selectedDrawingId: null,
      }
    })
    get()._scheduleSave()
  },

  redo: () => {
    set((s) => {
      if (s._redoStack.length === 0) return {}
      const next = s._redoStack[s._redoStack.length - 1]
      const snapshot = JSON.parse(JSON.stringify(s.drawings))
      return {
        drawings: next,
        _redoStack: s._redoStack.slice(0, -1),
        _undoStack: [...s._undoStack, snapshot],
        selectedDrawingId: null,
      }
    })
    get()._scheduleSave()
  },

  // ── Copy / Paste ─────────────────────────────────────────────────────────

  copySelected: () => {
    const { selectedDrawingId, drawings } = get()
    if (!selectedDrawingId) return
    for (const list of Object.values(drawings)) {
      const found = list.find((d) => d.id === selectedDrawingId)
      if (found) {
        set({ clipboard: JSON.parse(JSON.stringify(found)) as Drawing })
        return
      }
    }
  },

  paste: (key, crosshairPrice, crosshairTime) => {
    const { clipboard } = get()
    if (!clipboard) return

    const lastUnderscore = key.lastIndexOf('_')
    const symbol = key.slice(0, lastUnderscore)
    const timeframe = key.slice(lastUnderscore + 1)
    const newDrawing: Drawing = {
      ...JSON.parse(JSON.stringify(clipboard)) as Drawing,
      id: crypto.randomUUID(),
      symbol,
      timeframe,
    }

    // Translate points to crosshair position
    if (newDrawing.type === 'horizontal_line') {
      newDrawing.points = [{ time: crosshairTime, price: crosshairPrice }]
    } else if (newDrawing.points.length === 2) {
      const dx = crosshairTime - newDrawing.points[0].time
      const dy = crosshairPrice - newDrawing.points[0].price
      newDrawing.points = newDrawing.points.map((p) => ({
        time: p.time + dx,
        price: p.price + dy,
      }))
    }

    get().addDrawing(newDrawing)
  },

  // ── Export / Import ──────────────────────────────────────────────────────

  exportDrawings: () => {
    return JSON.stringify({ version: 1, drawings: get().drawings }, null, 2)
  },

  importDrawings: (json) => {
    try {
      const data = JSON.parse(json)
      const result = validateDrawingsExport(data)
      if (result.valid) {
        get()._pushHistory()
        set((s) => {
          // Merge imported drawings with existing
          const merged = { ...s.drawings }
          for (const [key, list] of Object.entries(result.valid!.drawings)) {
            const existing = merged[key] ?? []
            const existingIds = new Set(existing.map((d) => d.id))
            const newOnes = list.filter((d) => !existingIds.has(d.id))
            merged[key] = [...existing, ...newOnes]
          }
          return { drawings: merged }
        })
        get()._scheduleSave()
        return { ok: true, errors: result.errors }
      }
      return { ok: false, errors: result.errors }
    } catch (e) {
      return { ok: false, errors: [`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`] }
    }
  },

  // ── Persistence ──────────────────────────────────────────────────────────

  _scheduleSave: () => {
    const s = get()
    if (s._saveTimer) clearTimeout(s._saveTimer)
    set({ saveStatus: 'idle' })
    const timer = setTimeout(async () => {
      set({ saveStatus: 'saving' })
      try {
        await api.updateSettings({ drawings: get().drawings } as Partial<UserSettings>)
        set({ saveStatus: 'saved' })
        // Reset to idle after 2s
        setTimeout(() => {
          if (get().saveStatus === 'saved') set({ saveStatus: 'idle' })
        }, 2000)
      } catch {
        set({ saveStatus: 'error' })
      }
    }, 2000)
    set({ _saveTimer: timer })
  },

  _flushSave: () => {
    const s = get()
    if (s._saveTimer) {
      clearTimeout(s._saveTimer)
      set({ _saveTimer: null })
    }
    // Synchronous XHR is allowed in beforeunload and supports Authorization headers.
    // sendBeacon cannot send custom headers so it would fail on authenticated endpoints.
    try {
      const body = JSON.stringify({ drawings: s.drawings })
      const xhr = new XMLHttpRequest()
      xhr.open('PUT', '/api/settings', false) // false = synchronous
      xhr.setRequestHeader('Content-Type', 'application/json')
      const token = api.getAuthToken()
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      xhr.send(body)
    } catch {
      // Best effort — page is unloading anyway
    }
  },
}))
