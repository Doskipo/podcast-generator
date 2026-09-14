import { useEffect, useState } from 'react'
import { api } from '../api.js'
import Card from '../components/Card.jsx'
import CostByStageChart from '../components/dashboard/CostByStageChart.jsx'
import EpisodesPlaysChart from '../components/dashboard/EpisodesPlaysChart.jsx'
import KpiCard from '../components/dashboard/KpiCard.jsx'
import MockedBadge from '../components/dashboard/MockedBadge.jsx'
import RecentFailuresTable from '../components/dashboard/RecentFailuresTable.jsx'
import TopicDistributionChart from '../components/dashboard/TopicDistributionChart.jsx'

const STAGE_ORDER = ['rank', 'outline', 'script', 'critique', 'tts']

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

  useEffect(() => {
    let cancelled = false
    api
      .getMetricsSummary()
      .then((data) => {
        if (!cancelled) setSummary(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <Card title="Dashboard">
        <p className="text-sm text-red-600">{error}</p>
      </Card>
    )
  }

  if (!summary) {
    return (
      <Card title="Dashboard">
        <p className="text-sm text-slate-500">Loading…</p>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      {summary.has_mocked_data && (
        <div className="flex items-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm text-violet-800">
          <MockedBadge />
          Some of the numbers below include seeded demo data (
          <code className="rounded bg-violet-100 px-1 py-0.5 text-xs">podcast seed-metrics</code>) — mocked points are
          called out in each chart and table.
        </div>
      )}

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

      <Card
        title="Episodes & plays per day"
        actions={<span className="text-xs text-slate-400">last 30 days</span>}
      >
        <p className="mb-2 text-xs text-slate-500">
          Supply vs. demand over time — episodes with no corresponding plays means the pipeline is producing content
          nobody's listening to.
        </p>
        <EpisodesPlaysChart data={summary.daily_series} />
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card title="Cost by stage">
          <p className="mb-2 text-xs text-slate-500">
            Where the money actually goes — script writing deliberately uses the strongest (priciest) model; this is
            what confirms that trade-off is paying for itself rather than silently ballooning.
          </p>
          {summary.cost_by_stage.length === 0 ? (
            <p className="text-sm text-slate-500">No cost data yet — generate an episode or run seed-metrics.</p>
          ) : (
            <CostByStageChart data={pivotCostByStage(summary.cost_by_stage)} />
          )}
        </Card>

        <Card title="Topic distribution">
          <p className="mb-2 text-xs text-slate-500">
            Which interests are actually getting airtime — an interest that never shows up here needs better
            feeds/queries, not more weight in the profile.
          </p>
          {summary.topic_distribution.length === 0 ? (
            <p className="text-sm text-slate-500">No episodes with selected articles yet.</p>
          ) : (
            <TopicDistributionChart data={summary.topic_distribution} />
          )}
        </Card>
      </div>

      <Card title="Recent failures & no-content runs">
        <p className="mb-2 text-xs text-slate-500">
          The exact stage and reason each broken run stopped at — the fastest way to tell "the feed was empty this
          week" (fine) from "the OpenAI key expired" (not fine).
        </p>
        <RecentFailuresTable failures={summary.recent_failures} />
      </Card>
    </div>
  )
}
