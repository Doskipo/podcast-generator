import { Calendar, Mic2, Sparkles, Tags, Tv } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api.js'
import Button from '../components/Button.jsx'
import Card from '../components/Card.jsx'
import Field, { TextInput } from '../components/Field.jsx'
import HostEditor from '../components/HostEditor.jsx'
import InterestEditor from '../components/InterestEditor.jsx'
import SchedulePicker from '../components/SchedulePicker.jsx'
import Slider from '../components/Slider.jsx'
import Toggle from '../components/Toggle.jsx'

// Used when GET /profile 404s (no profile saved yet) — enough to satisfy the
// backend's required fields; fetch/llm/tts are left out entirely since the
// Profile model fills in their defaults server-side.
function blankProfile() {
  return {
    name: '',
    interests: [],
    podcast: {
      name: '',
      duration_minutes: 8,
      listener: { name: '' },
      hosts: [],
      recurring_bits: [],
      style: { humour: 1, depth: 2, tangents: true, banter: true },
      tone: '',
    },
    schedule: '0 7 * * *',
  }
}

export default function Settings() {
  const [profile, setProfile] = useState(null)
  const [voices, setVoices] = useState([])
  const [hostPresets, setHostPresets] = useState([])
  const [loadError, setLoadError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    api
      .getVoices()
      .then(setVoices)
      .catch(() => setVoices([]))

    api
      .getHostPresets()
      .then(setHostPresets)
      .catch(() => setHostPresets([]))

    api
      .getProfile()
      .then(setProfile)
      .catch((err) => {
        if (err.status === 404) {
          setProfile(blankProfile())
        } else {
          setLoadError(err.message)
        }
      })
  }, [])

  function updatePodcast(patch) {
    setProfile((p) => ({ ...p, podcast: { ...p.podcast, ...patch } }))
  }

  function updateStyle(patch) {
    updatePodcast({ style: { ...profile.podcast.style, ...patch } })
  }

  async function handleSave(e) {
    e.preventDefault()
    setSaving(true)
    setSaveError(null)
    setSaved(false)
    try {
      const savedProfile = await api.putProfile(profile)
      setProfile(savedProfile)
      setSaved(true)
    } catch (err) {
      setSaveError(err.message)
    } finally {
      setSaving(false)
    }
  }

  if (loadError) {
    return (
      <Card title="Settings">
        <p className="text-sm text-accent">Failed to load profile: {loadError}</p>
      </Card>
    )
  }
  if (!profile) {
    return (
      <Card title="Settings">
        <p className="text-sm text-ink/55">Loading…</p>
      </Card>
    )
  }

  return (
    <form onSubmit={handleSave} className="space-y-4">
      <h1 className="text-lg font-semibold text-surface">Settings</h1>

      <Card title="Show" icon={Tv}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Podcast name">
            <TextInput
              value={profile.podcast.name}
              onChange={(e) => updatePodcast({ name: e.target.value })}
              required
            />
          </Field>
          <Field label="Your name (listener)">
            <TextInput
              value={profile.podcast.listener.name}
              onChange={(e) => updatePodcast({ listener: { name: e.target.value } })}
              required
            />
          </Field>
          <Field label="Duration (minutes)">
            <TextInput
              type="number"
              min={1}
              value={profile.podcast.duration_minutes}
              onChange={(e) => updatePodcast({ duration_minutes: Number(e.target.value) })}
            />
          </Field>
          <Field label="Tone">
            <TextInput
              value={profile.podcast.tone}
              onChange={(e) => updatePodcast({ tone: e.target.value })}
              placeholder="e.g. curious, warm, a little irreverent"
            />
          </Field>
        </div>
      </Card>

      <Card title="Interests" icon={Tags}>
        <InterestEditor
          interests={profile.interests}
          onChange={(interests) => setProfile((p) => ({ ...p, interests }))}
        />
      </Card>

      <Card title="Hosts" icon={Mic2}>
        <HostEditor
          hosts={profile.podcast.hosts}
          voices={voices}
          presets={hostPresets}
          onChange={(hosts) => updatePodcast({ hosts })}
        />
      </Card>

      <Card title="Style" icon={Sparkles}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Slider label="Humour" value={profile.podcast.style.humour} min={0} max={3} onChange={(humour) => updateStyle({ humour })} />
          <Slider label="Depth" value={profile.podcast.style.depth} min={1} max={3} onChange={(depth) => updateStyle({ depth })} />
          <Toggle label="Tangents" checked={profile.podcast.style.tangents} onChange={(tangents) => updateStyle({ tangents })} />
          <Toggle label="Banter" checked={profile.podcast.style.banter} onChange={(banter) => updateStyle({ banter })} />
        </div>
      </Card>

      <Card title="Schedule" icon={Calendar}>
        <SchedulePicker value={profile.schedule} onChange={(schedule) => setProfile((p) => ({ ...p, schedule }))} />
      </Card>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </Button>
        {saved && <span className="text-sm text-accent">Saved.</span>}
        {saveError && <span className="text-sm text-accent">{saveError}</span>}
      </div>
    </form>
  )
}
