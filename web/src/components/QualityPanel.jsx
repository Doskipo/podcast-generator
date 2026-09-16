import { AlertTriangle } from 'lucide-react'
import MetricTooltip from './dashboard/MetricTooltip.jsx'

function formatPercent(fraction) {
  return `${Math.round(fraction * 100)}%`
}

function Stat({ label, value, why }) {
  return (
    <div>
      <div className="flex items-center text-[11px] font-medium uppercase tracking-wide text-ink/50">
        {label}
        {why && <MetricTooltip text={why} />}
      </div>
      <div className="text-sm font-semibold text-ink">{value}</div>
    </div>
  )
}

function JudgeScoreBlock({ label, score, reason }) {
  return (
    <div className="rounded-md bg-ink/5 p-3">
      <div className="flex items-baseline gap-1.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-ink/50">{label}</span>
        <span className="text-lg font-semibold text-accent">
          {score}
          <span className="text-xs text-ink/40">/5</span>
        </span>
      </div>
      <p className="mt-1 text-xs text-ink/70">{reason}</p>
    </div>
  )
}

// The episode card's "Quality" disclosure — proxy grounding/naturalness
// metrics plus the one cheap-model judge call. Every number here is a
// PROXY, not a verdict — see docs/decisions.md ("Quality metrics") for
// why each was chosen and what it can't measure; the disclaimer banner and
// the MetricTooltip "why" on each less-obvious stat are how that limit
// surfaces in the UI, not just in code comments.
export default function QualityPanel({ quality }) {
  if (!quality) {
    return <p className="text-sm text-ink/55">Quality metrics aren't available for this episode yet.</p>
  }

  const { grounding, naturalness, judge } = quality
  const retriedStages = Object.entries(grounding.stage_retries)
    .filter(([, retried]) => retried)
    .map(([stage]) => stage)

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-md border border-accent/20 bg-accent/5 p-2.5 text-xs text-ink/70">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" aria-hidden="true" />
        These are automated proxies, not a quality guarantee — they measure the pipeline's own
        signals (sourcing, self-correction, spoken-language patterns), not whether the episode is
        actually good. Read the judge's reasons below rather than just trusting the numbers.
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <JudgeScoreBlock label="Naturalness" score={judge.naturalness.score} reason={judge.naturalness.reason} />
        <JudgeScoreBlock label="Stance clarity" score={judge.stance_clarity.score} reason={judge.stance_clarity.reason} />
      </div>

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink/40">Grounding</h4>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Stat label="Sources" value={grounding.source_count} />
          <Stat
            label="Evergreen"
            value={formatPercent(grounding.evergreen_share)}
            why="Share of segments built from a background primer instead of fresh news — not bad on its own, but consistently high means an interest isn't finding new content."
          />
          <Stat label="Critique flags" value={grounding.critique_flags} />
          <Stat
            label="Rewrite rate"
            value={formatPercent(grounding.critique_rewrite_rate)}
            why="Share of the original script's lines critique had to flag and rewrite — a proxy for how far the first draft was from acceptable, not for how good the final script is."
          />
          <Stat
            label="Fact-drift flags"
            value={grounding.fact_drift_flags}
            why="Lines perform's own fact-check pass judged to have changed meaning while rewriting for performance."
          />
          <Stat
            label="Retries"
            value={grounding.total_retries}
            why={`Stage(s) that needed generate_with_retry's one retry: ${retriedStages.length ? retriedStages.join(', ') : 'none'}.`}
          />
          <Stat
            label="Word overrun"
            value={grounding.word_overrun > 0 ? `+${grounding.word_overrun}w` : 'within cap'}
            why="Words the final performance exceeds perform's word cap by, after one regeneration attempt. A quality signal, not a correctness invariant — an overrun never fails the run."
          />
        </div>
      </div>

      {grounding.repairs.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink/40">Auto-repaired</h4>
          <ul className="space-y-1 text-xs text-ink/70">
            {grounding.repairs.map((repair, i) => (
              // eslint-disable-next-line react/no-array-index-key -- repairs are plain strings with no stable id
              <li key={i} className="rounded-md bg-ink/5 px-2 py-1">
                {repair}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink/40">Naturalness proxies</h4>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label="Audio tags"
            value={`${naturalness.audio_tag_density_per_100_words.toFixed(1)}/100w`}
            why="Bracketed audio tags like [laughs] per 100 words of performed text."
          />
          <Stat
            label="Interjections"
            value={formatPercent(naturalness.interjection_or_dash_share)}
            why="Share of lines that open with a disfluency (wait, i mean, look...) or end in a trailing em dash — a spoken-language marker, not a naturalness score by itself."
          />
          <Stat
            label="Host balance"
            value={naturalness.host_balance.toFixed(2)}
            why="Ratio of the two hosts' mean words-per-line — 1.0 is perfectly even. Can't tell a deliberately terse persona from an actually-neglected host."
          />
          <Stat
            label="Catchphrases"
            value={naturalness.catchphrase_count}
            why="Recurring (2+) verbatim 4-word phrases per host — an emergent-repetition proxy, not necessarily a deliberate running bit."
          />
        </div>
      </div>
    </div>
  )
}
