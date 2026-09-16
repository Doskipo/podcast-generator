import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART, SERIES } from '../../lib/chartColors.js'

function formatDay(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-ink/10 bg-surface px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-medium text-ink">{formatDay(label)}</div>
      {payload.map((entry) => (
        <div key={entry.dataKey} className="flex items-center gap-1.5 text-ink/70">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: entry.color }} />
          {entry.name}: {entry.value}/5
        </div>
      ))}
    </div>
  )
}

// The judge's two scores, per episode, over time — the rest of the quality
// proxies (grounding/naturalness numbers) are in QualityTable below, not
// crowded onto this chart. See docs/decisions.md ("Quality metrics") —
// these are proxies, not a verdict.
export default function QualityChart({ data }) {
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDay}
            stroke={CHART.axis}
            tick={{ fill: CHART.inkMuted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
          />
          <YAxis
            domain={[1, 5]}
            allowDecimals={false}
            stroke={CHART.axis}
            tick={{ fill: CHART.inkMuted, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip content={<ChartTooltip />} />
          <Legend wrapperStyle={{ fontSize: 12, color: CHART.ink }} />
          <Line
            type="monotone"
            dataKey="naturalness_score"
            name="Naturalness"
            stroke={SERIES.strong}
            strokeWidth={2}
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="stance_clarity_score"
            name="Stance clarity"
            stroke={SERIES.soft}
            strokeWidth={2}
            strokeDasharray="4 3"
            dot={{ r: 3 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
