import HostAvatar, { speakerColorClass } from './HostAvatar.jsx'

// First non-empty line of a host's persona — the same "brief, not the full
// paragraph" convention outline.py's _render_host_brief uses server-side.
function firstLine(persona) {
  return persona
    ?.split('\n')
    .map((line) => line.trim())
    .find(Boolean)
}

// The "About the hosts" section of an episode card — split out of
// ScriptView so it can be its own collapsible disclosure alongside
// Transcript/Sources. See docs/decisions.md ("Episode card: hero player,
// collapsed sections").
export default function HostBios({ hosts }) {
  if (!hosts?.length) return <p className="text-sm text-ink/55">No hosts configured yet.</p>

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:gap-6">
      {hosts.map((host, i) => (
        <div key={host.name} className="flex items-start gap-2">
          <HostAvatar name={host.name} index={i} />
          <div>
            <div className={`text-sm font-semibold ${speakerColorClass(i)}`}>{host.name}</div>
            <p className="text-xs text-ink/60">{firstLine(host.persona)}</p>
          </div>
        </div>
      ))}
    </div>
  )
}
