import HostAvatar, { speakerColorClass } from './HostAvatar.jsx'

// First non-empty line of a host's persona — the same "brief, not the full
// paragraph" convention outline.py's _render_host_brief uses server-side.
function firstLine(persona) {
  return persona
    ?.split('\n')
    .map((line) => line.trim())
    .find(Boolean)
}

function Lines({ lines, hostIndex }) {
  return (
    <ol className="space-y-2">
      {lines.map((line, i) => {
        const index = hostIndex.get(line.speaker) ?? 0
        return (
          // eslint-disable-next-line react/no-array-index-key -- lines have no id, order is stable within one script
          <li key={i} className="flex items-start gap-2 text-sm">
            <HostAvatar name={line.speaker} index={index} size="sm" />
            <span>
              <span className={`font-medium ${speakerColorClass(index)}`}>{line.speaker}:</span>{' '}
              <span className="text-ink/80">{line.text}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

export default function ScriptView({ script, hosts = [] }) {
  if (!script) return <p className="text-sm text-ink/55">Script not ready yet.</p>

  const hostIndex = new Map(hosts.map((h, i) => [h.name, i]))

  return (
    <div className="space-y-5">
      <h3 className="font-semibold text-ink">{script.title}</h3>

      {hosts.length > 0 && (
        <div className="flex flex-col gap-2 rounded-md bg-ink/5 p-3 sm:flex-row sm:gap-6">
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
      )}

      <div>
        <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink/40">Cold open</h4>
        <Lines lines={script.cold_open} hostIndex={hostIndex} />
      </div>

      {script.segments.map((segment, i) => (
        // eslint-disable-next-line react/no-array-index-key -- segments have no id, order is stable within one script
        <div key={i}>
          <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink/40">{segment.headline}</h4>
          <Lines lines={segment.lines} hostIndex={hostIndex} />
        </div>
      ))}

      <div>
        <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink/40">Outro</h4>
        <Lines lines={script.outro} hostIndex={hostIndex} />
      </div>
    </div>
  )
}
