import { useState } from 'react'
import { api } from '../api.js'
import Button from './Button.jsx'
import Field, { TextArea, TextInput } from './Field.jsx'
import Slider from './Slider.jsx'

function InterestRow({ interest, onChange, onRemove }) {
  const [suggesting, setSuggesting] = useState(false)
  const [suggestError, setSuggestError] = useState(null)

  async function handleSuggest() {
    setSuggesting(true)
    setSuggestError(null)
    try {
      const result = await api.suggestInterest(interest.topic, interest.description || null)
      onChange({ ...interest, description: result.description, queries: result.queries })
    } catch (err) {
      setSuggestError(err.message)
    } finally {
      setSuggesting(false)
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-slate-200 p-3">
      <div className="flex items-start justify-between gap-3">
        <Field label="Topic" className="flex-1">
          <TextInput
            value={interest.topic}
            onChange={(e) => onChange({ ...interest, topic: e.target.value })}
            placeholder="e.g. mechanistic interpretability"
          />
        </Field>
        <Button variant="danger" type="button" onClick={onRemove} className="mt-6">
          Remove
        </Button>
      </div>

      <Slider
        label="Weight"
        value={interest.weight}
        min={0}
        max={1}
        step={0.05}
        onChange={(weight) => onChange({ ...interest, weight })}
      />

      <Field label="Description (optional)">
        <TextArea
          value={interest.description ?? ''}
          onChange={(e) => onChange({ ...interest, description: e.target.value })}
          placeholder="What this interest actually means — scope, angle, what counts as relevant"
        />
      </Field>

      <div className="flex items-center gap-2">
        <Button type="button" variant="secondary" onClick={handleSuggest} disabled={!interest.topic || suggesting}>
          {suggesting ? 'Suggesting…' : 'Suggest'}
        </Button>
        {suggestError && <span className="text-xs text-red-600">{suggestError}</span>}
      </div>

      {interest.queries?.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {interest.queries.map((q) => (
            <span key={q} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
              {q}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function InterestEditor({ interests, onChange }) {
  function updateAt(index, next) {
    onChange(interests.map((it, i) => (i === index ? next : it)))
  }

  function removeAt(index) {
    onChange(interests.filter((_, i) => i !== index))
  }

  function add() {
    onChange([...interests, { topic: '', weight: 0.5, description: null, feeds: [], queries: null }])
  }

  return (
    <div className="space-y-3">
      {interests.map((interest, i) => (
        // eslint-disable-next-line react/no-array-index-key -- rows have no stable id until saved
        <InterestRow key={i} interest={interest} onChange={(next) => updateAt(i, next)} onRemove={() => removeAt(i)} />
      ))}
      <Button type="button" variant="secondary" onClick={add}>
        + Add interest
      </Button>
    </div>
  )
}
