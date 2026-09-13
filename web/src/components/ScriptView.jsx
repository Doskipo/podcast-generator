function Lines({ lines }) {
  return (
    <ol className="space-y-1.5">
      {lines.map((line, i) => (
        // eslint-disable-next-line react/no-array-index-key -- lines have no id, order is stable within one script
        <li key={i} className="text-sm">
          <span className="font-medium text-slate-700">{line.speaker}:</span>{' '}
          <span className="text-slate-600">{line.text}</span>
        </li>
      ))}
    </ol>
  )
}

export default function ScriptView({ script }) {
  if (!script) return <p className="text-sm text-slate-500">Script not ready yet.</p>

  return (
    <div className="space-y-4">
      <h3 className="font-semibold text-slate-900">{script.title}</h3>

      <div>
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Cold open</h4>
        <Lines lines={script.cold_open} />
      </div>

      {script.segments.map((segment, i) => (
        // eslint-disable-next-line react/no-array-index-key -- segments have no id, order is stable within one script
        <div key={i}>
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{segment.headline}</h4>
          <Lines lines={segment.lines} />
        </div>
      ))}

      <div>
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Outro</h4>
        <Lines lines={script.outro} />
      </div>
    </div>
  )
}
