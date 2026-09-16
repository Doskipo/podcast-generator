function formatPercent(fraction) {
  return `${Math.round(fraction * 100)}%`
}

// The grounding/naturalness proxies that don't fit on QualityChart —
// one row per real episode with a quality.json. See docs/decisions.md
// ("Quality metrics"): every column here is a proxy, not a verdict.
export default function QualityTable({ points }) {
  if (points.length === 0) {
    return <p className="text-sm text-ink/55">No quality data yet — it's computed once an episode reaches the quality stage (after perform, before tts).</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-ink/40">
            <th className="py-1.5 pr-3 font-medium">Episode</th>
            <th className="py-1.5 pr-3 font-medium">Sources</th>
            <th className="py-1.5 pr-3 font-medium">Evergreen</th>
            <th className="py-1.5 pr-3 font-medium">Critique flags</th>
            <th className="py-1.5 pr-3 font-medium">Rewrite rate</th>
            <th className="py-1.5 pr-3 font-medium">Fact drift</th>
            <th className="py-1.5 pr-3 font-medium">Retries</th>
            <th className="py-1.5 pr-3 font-medium">Audio tags</th>
            <th className="py-1.5 pr-3 font-medium">Interjections</th>
            <th className="py-1.5 pr-3 font-medium">Host balance</th>
            <th className="py-1.5 font-medium">Catchphrases</th>
          </tr>
        </thead>
        <tbody>
          {[...points].reverse().map((p) => (
            <tr key={p.episode_id} className="border-t border-ink/8">
              <td className="py-1.5 pr-3 font-medium text-ink">{p.date}</td>
              <td className="py-1.5 pr-3 text-ink/70">{p.source_count}</td>
              <td className="py-1.5 pr-3 text-ink/70">{formatPercent(p.evergreen_share)}</td>
              <td className="py-1.5 pr-3 text-ink/70">{p.critique_flags}</td>
              <td className="py-1.5 pr-3 text-ink/70">{formatPercent(p.critique_rewrite_rate)}</td>
              <td className="py-1.5 pr-3 text-ink/70">{p.fact_drift_flags}</td>
              <td className="py-1.5 pr-3 text-ink/70">{p.total_retries}</td>
              <td className="py-1.5 pr-3 text-ink/70">{p.audio_tag_density_per_100_words.toFixed(1)}/100w</td>
              <td className="py-1.5 pr-3 text-ink/70">{formatPercent(p.interjection_or_dash_share)}</td>
              <td className="py-1.5 pr-3 text-ink/70">{p.host_balance.toFixed(2)}</td>
              <td className="py-1.5 text-ink/70">{p.catchphrase_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
