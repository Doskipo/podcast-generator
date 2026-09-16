import { useEffect, useState } from 'react'
import { api } from '../api.js'
import Card from '../components/Card.jsx'
import CostByStageChart from '../components/dashboard/CostByStageChart.jsx'
import EpisodesPlaysChart from '../components/dashboard/EpisodesPlaysChart.jsx'
import KpiCard from '../components/dashboard/KpiCard.jsx'
import MockedBadge from '../components/dashboard/MockedBadge.jsx'
import RecentFailuresTable from '../components/dashboard/RecentFailuresTable.jsx'
import TopicDistributionChart from '../components/dashboard/TopicDistributionChart.jsx'

const STAGE_ORDER = ['rank', 'outline', 'script', 'critique', 'perform', 'tts']

// Pivots [{stage, provider, cost_usd}, ...] into recharts rows
// [{stage, openai, elevenlabs}, ...], in pipeline order.
function pivotCostByStage(rows) {
  const byStage = new Map()
  for (const row of rows) {
    if (!byStage.has(row.stage)) byStage.set(row.stage, { stage: row.stage })
    byStage.get(row.stage)[row.provider] = row.cost_usd
  }
  return STAGE_ORDER.filter((stage) => byStage.has(stage)).map((stage) => byStage.get(stage))
}

function formatPercent(fraction) {
  return fraction == null ? '—' : `${Math.round(fraction * 100)}%`
}

function formatUsd(amount) {
  return amount == null ? '—' : `$${amount.toFixed(3)}`
}

export default function Dashboard() {
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  // Dashboard KPIs default to real episodes only — see docs/decisions.md
  // ("Re-measured words-per-minute, dashboard mocked-data toggle").
  const [includeMocked, setIncludeMocked] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .getMetricsSummary(includeMocked)
      .then((data) => {
        if (!cancelled) setSummary(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [includeMocked])

  if (error) {
    return (
      <Card title="Dashboard">
        <p className="text-sm text-accent">{error}</p>
      </Card>
    )
  }

  if (!summary) {
    return (
      <Card title="Dashboard">
        <p className="text-sm text-ink/55">Loading…</p>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold text-surface">Dashboard</h1>

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ink/10 bg-surface px-4 py-2.5 text-sm">
        <span className="font-medium text-ink">
          Showing: {summary.include_mocked ? 'real episodes + mocked demo data' : 'real episodes only'}
        </span>
        {summary.has_mocked_data && (
          <label className="flex items-center gap-2 text-ink/70">
            <input
              type="checkbox"
              checked={includeMocked}
              onChange={(e) => setIncludeMocked(e.target.checked)}
              className="h-4 w-4 rounded border-ink/20 accent-accent"
            />
            Include mocked usage
          </label>
        )}
      </div>

      {summary.include_mocked && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-accent/20 bg-accent/10 px-4 py-2.5 text-sm text-ink">
          <MockedBadge />
          Some of the numbers below include seeded demo data (
          <code className="rounded bg-accent/15 px-1 py-0.5 text-xs">podcast seed-metrics</code>) — mocked points are
          called out in each chart and table.
        </div>
      )}

      {summary.total_episodes === 0 ? (
        <Card>
          <p className="text-sm text-ink/55">
            No episode data yet — generate an episode (or run <code className="rounded bg-ink/5 px-1 py-0.5 text-xs">podcast seed-metrics</code> for demo data) to see KPIs here.
          </p>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <KpiCard
              label="Episodes"
              value={`${summary.done}/${summary.total_episodes}`}
              why="How much content the pipeline is actually producing end to end — a stalled number usually means a stage or the scheduler is silently broken, not that there's simply no news."
            />
            <KpiCard
              label="Completion rate"
              value={formatPercent(summary.completion_rate)}
              why="Did listeners actually finish what got generated, or is the pipeline producing episodes nobody wants to finish — plays without completions is a script/quality problem, not a generation problem."
            />
            <KpiCard
              label="D7 retention"
              value={formatPercent(summary.d7_retention)}
              why="Whether people come back a week later — the clearest signal a personal podcast has earned a place in someone's routine, versus being tried once and dropped."
            />
            <KpiCard
              label="Cost / episode"
              value={formatUsd(summary.avg_cost_per_episode_usd)}
              why="What one episode actually costs across both providers combined — the number that decides whether this scales past a personal project."
            />
          </div>

          <Card title="Episodes & plays per day" actions={<span className="text-xs text-ink/40">last 30 days</span>}>
            <p className="mb-2 text-xs text-ink/55">
              Supply vs. demand over time — episodes with no corresponding plays means the pipeline is producing
              content nobody's listening to.
            </p>
            <EpisodesPlaysChart data={summary.daily_series} />
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card title="Cost by stage">
              <p className="mb-2 text-xs text-ink/55">
                Where the money actually goes — script writing deliberately uses the strongest (priciest) model; this
                is what confirms that trade-off is paying for itself rather than silently ballooning.
              </p>
              {summary.cost_by_stage.length === 0 ? (
                <p className="text-sm text-ink/55">No cost data yet — generate an episode to see a breakdown here.</p>
              ) : (
                <CostByStageChart data={pivotCostByStage(summary.cost_by_stage)} />
              )}
            </Card>

            <Card title="Topic distribution">
              <p className="mb-2 text-xs text-ink/55">
                Which interests are actually getting airtime — an interest that never shows up here needs better
                feeds/queries, not more weight in the profile.
              </p>
              {summary.topic_distribution.length === 0 ? (
                <p className="text-sm text-ink/55">No episodes with selected articles yet.</p>
              ) : (
                <TopicDistributionChart data={summary.topic_distribution} />
              )}
            </Card>
          </div>

          <Card title="Recent failures & no-content runs">
            <p className="mb-2 text-xs text-ink/55">
              The exact stage and reason each broken run stopped at — the fastest way to tell "the feed was empty
              this week" (fine) from "the OpenAI key expired" (not fine).
            </p>
            <RecentFailuresTable failures={summary.recent_failures} />
          </Card>
        </>
      )}
    </div>
  )
}
