import { useState } from 'react'
import Button from './Button.jsx'
import Field, { Select, TextArea, TextInput } from './Field.jsx'

function HostRow({ host, voices, presets, onChange, onRemove }) {
  // Remounted (via `key`) after every apply so the select snaps back to the
  // placeholder — a preset fills the row once, it isn't a binding the row
  // stays "on" afterward, and the row stays fully editable from there.
  const [presetPickerKey, setPresetPickerKey] = useState(0)

  function applyPreset(presetName) {
    const preset = presets.find((p) => p.name === presetName)
    if (!preset) return
    onChange({ ...host, name: preset.name, voice_id: preset.voice_id, persona: preset.persona, home_turf: preset.home_turf })
    setPresetPickerKey((k) => k + 1)
  }

  return (
    <div className="space-y-3 rounded-md border border-ink/15 p-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
        <Field label="Name" className="flex-1">
          <TextInput value={host.name} onChange={(e) => onChange({ ...host, name: e.target.value })} />
        </Field>
        <Field label="Voice" className="flex-1">
          <Select value={host.voice_id} onChange={(e) => onChange({ ...host, voice_id: e.target.value })}>
            <option value="" disabled>
              Choose a voice…
            </option>
            {voices.map((v) => (
              <option key={v.voice_id} value={v.voice_id}>
                {v.label}
              </option>
            ))}
          </Select>
        </Field>
        {presets.length > 0 && (
          <Field label="Preset" className="flex-1">
            <Select key={presetPickerKey} defaultValue="" onChange={(e) => applyPreset(e.target.value)}>
              <option value="" disabled>
                Choose a preset…
              </option>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Button variant="danger" type="button" onClick={onRemove} className="self-end sm:mt-6 sm:self-auto">
          Remove
        </Button>
      </div>

      <Field label="Persona">
        <TextArea
          value={host.persona}
          onChange={(e) => onChange({ ...host, persona: e.target.value })}
          placeholder="Brief this host like a voice actor: background, speech pattern, tendencies…"
          className="min-h-32"
        />
      </Field>
    </div>
  )
}

export default function HostEditor({ hosts, voices, presets = [], onChange }) {
  function updateAt(index, next) {
    onChange(hosts.map((h, i) => (i === index ? next : h)))
  }

  function removeAt(index) {
    onChange(hosts.filter((_, i) => i !== index))
  }

  function add() {
    onChange([...hosts, { name: '', voice_id: voices[0]?.voice_id ?? '', persona: '', home_turf: [] }])
  }

  return (
    <div className="space-y-3">
      {hosts.length === 0 && (
        <p className="text-sm text-ink/55">No hosts yet — this podcast needs exactly two before it can generate an episode.</p>
      )}
      {hosts.map((host, i) => (
        // eslint-disable-next-line react/no-array-index-key -- rows have no stable id until saved
        <HostRow key={i} host={host} voices={voices} presets={presets} onChange={(next) => updateAt(i, next)} onRemove={() => removeAt(i)} />
      ))}
      <Button type="button" variant="secondary" onClick={add}>
        + Add host
      </Button>
    </div>
  )
}
