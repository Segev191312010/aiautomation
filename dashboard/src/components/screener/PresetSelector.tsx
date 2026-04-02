import React, { useState } from 'react'
import { useScreenerStore } from '@/store'
import { useToast } from '@/components/ui/ToastProvider'

// Inline SVG icons — no external icon library required
function XIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" className="w-3 h-3">
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  )
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="w-2.5 h-2.5">
      <rect x="3" y="11" width="18" height="11" rx="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  )
}

function SpinnerIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" className="w-3 h-3 animate-spin">
      <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
    </svg>
  )
}

export default function PresetSelector() {
  const { presets, applyPreset, savePreset, deletePreset, filters } = useScreenerStore()
  const toast = useToast()

  // Save flow
  const [showSaveInput, setShowSaveInput] = useState(false)
  const [name, setName] = useState('')
  const [saving, setSaving] = useState(false)

  // Per-item delete loading: stores the id currently being deleted
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const handleSaveClick = () => {
    setShowSaveInput(true)
    // Focus is handled via autoFocus on the input
  }

  const handleSaveConfirm = async () => {
    if (!name.trim() || saving) return
    setSaving(true)
    try {
      await savePreset(name.trim())
      setName('')
      setShowSaveInput(false)
      toast.success('Preset saved')
    } catch {
      toast.error('Failed to save preset')
    } finally {
      setSaving(false)
    }
  }

  const handleSaveCancel = () => {
    setName('')
    setShowSaveInput(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSaveConfirm()
    if (e.key === 'Escape') handleSaveCancel()
  }

  const handleApply = (presetId: string) => {
    const preset = presets.find((item) => item.id === presetId)
    if (preset) applyPreset(preset)
  }

  const handleDelete = async (id: string) => {
    if (deletingId) return
    setDeletingId(id)
    try {
      await deletePreset(id)
      toast.success('Preset deleted')
    } catch {
      toast.error('Failed to delete preset')
    } finally {
      setDeletingId(null)
    }
  }

  const userPresets = presets.filter((item) => !item.built_in)
  const builtInPresets = presets.filter((item) => item.built_in)
  const hasFilters = filters.length > 0

  return (
    <div className="space-y-4">
      {/* Built-in presets — locked, read-only */}
      {builtInPresets.length > 0 && (
        <div>
          <div className="flex items-center gap-1.5 text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">
            <LockIcon />
            <span>Built In</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {builtInPresets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                onClick={() => handleApply(preset.id)}
                className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-3 py-2 text-[11px] font-sans font-medium text-zinc-400 transition-colors hover:border-zinc-700 hover:text-zinc-200"
              >
                {preset.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Custom saved presets */}
      {userPresets.length > 0 && (
        <div>
          <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Saved</div>
          <div className="mt-2 space-y-1.5">
            {userPresets.map((preset) => {
              const isDeleting = deletingId === preset.id
              return (
                <div
                  key={preset.id}
                  className={[
                    'flex items-center justify-between gap-2 rounded-lg border px-3 py-2.5 transition-colors',
                    isDeleting
                      ? 'border-zinc-800 bg-zinc-900/40 opacity-60'
                      : 'border-zinc-800 bg-zinc-900 hover:border-zinc-700',
                  ].join(' ')}
                >
                  <button
                    type="button"
                    onClick={() => !isDeleting && handleApply(preset.id)}
                    disabled={isDeleting}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-not-allowed"
                  >
                    <span className="shrink-0 rounded-full bg-zinc-800 px-2 py-0.5 text-[10px] font-mono text-zinc-400">
                      Custom
                    </span>
                    <span className="truncate text-[11px] font-sans font-medium text-zinc-100">
                      {preset.name}
                    </span>
                  </button>

                  <button
                    type="button"
                    onClick={() => handleDelete(preset.id)}
                    disabled={!!deletingId}
                    title={isDeleting ? 'Deleting…' : `Delete "${preset.name}"`}
                    className={[
                      'flex shrink-0 items-center justify-center rounded p-1 transition-colors',
                      isDeleting
                        ? 'text-zinc-500 cursor-not-allowed'
                        : deletingId
                          ? 'text-zinc-600 cursor-not-allowed'
                          : 'text-zinc-500 hover:bg-red-500/15 hover:text-red-400',
                    ].join(' ')}
                  >
                    {isDeleting ? <SpinnerIcon /> : <XIcon />}
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Save current screen */}
      <div className="rounded-lg border border-zinc-700 bg-zinc-800/50 p-3">
        {showSaveInput ? (
          <div className="space-y-2">
            <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">
              Name this preset
            </div>
            <div className="flex items-center gap-2">
              <input
                // eslint-disable-next-line jsx-a11y/no-autofocus
                autoFocus
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="e.g. Momentum + liquidity"
                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-xs font-sans text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-600 focus:outline-none"
              />
              <button
                type="button"
                onClick={handleSaveConfirm}
                disabled={saving || !name.trim()}
                className={[
                  'rounded-lg border px-3 py-2 text-[11px] font-sans font-medium transition-colors',
                  saving || !name.trim()
                    ? 'cursor-not-allowed border-zinc-800 bg-zinc-800 text-zinc-500'
                    : 'border-zinc-600 bg-zinc-700 text-white hover:bg-zinc-600',
                ].join(' ')}
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                onClick={handleSaveCancel}
                disabled={saving}
                className="flex items-center justify-center rounded p-2 text-zinc-500 transition-colors hover:text-zinc-300 disabled:cursor-not-allowed"
                title="Cancel"
              >
                <XIcon />
              </button>
            </div>
          </div>
        ) : (
          <button
            type="button"
            onClick={handleSaveClick}
            disabled={!hasFilters}
            title={!hasFilters ? 'Add at least one filter first' : 'Save current filters as a preset'}
            className={[
              'flex w-full items-center justify-center gap-2 rounded-lg py-2 text-[11px] font-sans font-medium transition-colors',
              !hasFilters
                ? 'cursor-not-allowed text-zinc-600'
                : 'text-zinc-400 hover:text-zinc-100',
            ].join(' ')}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5">
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
              <polyline points="17 21 17 13 7 13 7 21" />
              <polyline points="7 3 7 8 15 8" />
            </svg>
            Save current filters as preset
          </button>
        )}
      </div>
    </div>
  )
}
