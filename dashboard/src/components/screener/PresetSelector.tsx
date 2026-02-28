import React, { useState } from 'react'
import { useScreenerStore } from '@/store'
import { useToast } from '@/components/ui/ToastProvider'

export default function PresetSelector() {
  const { presets, applyPreset, savePreset, deletePreset, filters } = useScreenerStore()
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')

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

  const handleApply = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const id = e.target.value
    if (!id) return
    const preset = presets.find((p) => p.id === id)
    if (preset) applyPreset(preset)
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <select
        onChange={handleApply}
        defaultValue=""
        className="px-2 py-1.5 bg-terminal-bg border border-terminal-border rounded text-xs font-mono text-terminal-text focus:border-terminal-blue focus:outline-none"
      >
        <option value="">Load preset...</option>
        {presets.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} {p.built_in ? '(built-in)' : ''}
          </option>
        ))}
      </select>

      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Preset name"
        className="px-2 py-1.5 bg-terminal-bg border border-terminal-border rounded text-xs font-mono text-terminal-text placeholder-terminal-ghost focus:border-terminal-blue focus:outline-none w-32"
      />
      <button
        onClick={handleSave}
        disabled={saving || !name.trim() || filters.length === 0}
        className="px-3 py-1.5 rounded text-xs font-mono font-semibold bg-terminal-muted text-terminal-dim hover:text-terminal-text hover:bg-terminal-surface disabled:opacity-40 transition-colors"
      >
        {saving ? 'Saving...' : 'Save'}
      </button>

      {presets.filter((p) => !p.built_in).length > 0 && (
        <select
          onChange={async (e) => {
            if (e.target.value) {
              try {
                await deletePreset(e.target.value)
                toast.success('Preset deleted')
              } catch {
                toast.error('Failed to delete preset')
              }
            }
            e.target.value = ''
          }}
          defaultValue=""
          className="px-2 py-1.5 bg-terminal-bg border border-terminal-border rounded text-xs font-mono text-terminal-dim focus:border-terminal-blue focus:outline-none"
        >
          <option value="">Delete...</option>
          {presets.filter((p) => !p.built_in).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      )}
    </div>
  )
}
