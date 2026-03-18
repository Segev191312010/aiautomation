import React, { useState } from 'react'
import { useScreenerStore } from '@/store'
import { useToast } from '@/components/ui/ToastProvider'

export default function PresetSelector() {
  const { presets, applyPreset, savePreset, deletePreset, filters } = useScreenerStore()
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const [showDelete, setShowDelete] = useState(false)

  const handleSave = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      await savePreset(name.trim())
      setName('')
      toast.success('Preset saved')
    } catch {
      toast.error('Failed to save preset')
    } finally {
      setSaving(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSave()
  }

  const handleApply = (presetId: string) => {
    const preset = presets.find((item) => item.id === presetId)
    if (preset) applyPreset(preset)
  }

  const handleDelete = async (id: string) => {
    try {
      await deletePreset(id)
      toast.success('Preset deleted')
    } catch {
      toast.error('Failed to delete preset')
    }
  }

  const userPresets = presets.filter((item) => !item.built_in)
  const builtInPresets = presets.filter((item) => item.built_in)

  return (
    <div className="space-y-4">
      {builtInPresets.length > 0 && (
        <div>
          <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Built In</div>
          <div className="mt-2 flex flex-wrap gap-2">
            {builtInPresets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                onClick={() => handleApply(preset.id)}
                className="rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-[11px] font-sans font-medium text-zinc-200 transition-colors hover:border-zinc-700 hover:text-zinc-50"
              >
                {preset.name}
              </button>
            ))}
          </div>
        </div>
      )}

      {userPresets.length > 0 && (
        <div>
          <div className="flex items-center justify-between gap-3">
            <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Saved</div>
            <button
              type="button"
              onClick={() => setShowDelete((value) => !value)}
              className="text-[11px] font-sans text-zinc-400 transition-colors hover:text-zinc-50"
            >
              {showDelete ? 'Done' : 'Manage'}
            </button>
          </div>
          <div className="mt-2 space-y-2">
            {userPresets.map((preset) => (
              <div key={preset.id} className="flex items-center justify-between gap-3 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2.5">
                <button
                  type="button"
                  onClick={() => !showDelete && handleApply(preset.id)}
                  className="flex min-w-0 items-center gap-2 text-left"
                >
                  <span className="rounded-full bg-zinc-800 px-2 py-1 text-[10px] font-mono text-zinc-400">Custom</span>
                  <span className="truncate text-[11px] font-sans font-medium text-zinc-100">{preset.name}</span>
                </button>
                {showDelete && (
                  <button
                    type="button"
                    onClick={() => handleDelete(preset.id)}
                    className="rounded-lg border border-red-200 bg-red-500/10 px-2 py-1 text-[11px] font-sans text-red-400 transition-colors hover:bg-red-500/15"
                  >
                    Delete
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-lg border border-[#E8E4DF] bg-[#FAF8F5] p-4">
        <div className="text-[10px] font-sans uppercase tracking-[0.18em] text-zinc-500">Save Current Screen</div>
        <div className="mt-3 flex items-center gap-2">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Momentum + liquidity"
            className="flex-1 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs font-sans text-zinc-100 placeholder:text-zinc-500 focus:border-zinc-700 focus:outline-none"
          />
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !name.trim() || filters.length === 0}
            className={
              saving || !name.trim() || filters.length === 0
                ? 'rounded-lg border border-zinc-800 bg-zinc-800 px-3 py-2 text-[11px] font-sans font-medium text-zinc-400 cursor-not-allowed'
                : 'rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-[11px] font-sans font-medium text-white transition-colors hover:bg-zinc-900'
            }
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
