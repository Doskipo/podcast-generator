import Button from './Button.jsx'
import Field, { Select, TextArea, TextInput } from './Field.jsx'

function HostRow({ host, voices, onChange, onRemove }) {
  return (
    <div className="space-y-3 rounded-md border border-slate-200 p-3">
      <div className="flex items-start gap-3">
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
        <Button variant="danger" type="button" onClick={onRemove} className="mt-6">
          Remove
        </Button>
      </div>

      <Field label="Persona">
        <TextArea
          value={host.persona}
          onChange={(e) => onChange({ ...host, persona: e.target.value })}
          placeholder="Brief this host like a voice actor: background, speech pattern, catchphrase…"
          className="min-h-32"
        />
      </Field>
    </div>
  )
}

export default function HostEditor({ hosts, voices, onChange }) {
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
      {hosts.map((host, i) => (
        // eslint-disable-next-line react/no-array-index-key -- rows have no stable id until saved
        <HostRow key={i} host={host} voices={voices} onChange={(next) => updateAt(i, next)} onRemove={() => removeAt(i)} />
      ))}
      <Button type="button" variant="secondary" onClick={add}>
        + Add host
      </Button>
    </div>
  )
}
