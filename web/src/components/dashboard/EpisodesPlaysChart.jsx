import { CartesianGrid, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART, SERIES } from '../../lib/chartColors.js'

function formatDay(iso) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const mocked = payload[0]?.payload?.mocked
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 font-medium text-slate-900">
        {formatDay(label)}
        {mocked && <span className="ml-1 text-violet-600">(mocked)</span>}
      </div>
      {payload.map((entry) => (
        <div key={entry.dataKey} className="flex items-center gap-1.5 text-slate-600">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: entry.color }} />
          {entry.name}: {entry.value}
        </div>
      ))}
    </div>
  )
}

export default function EpisodesPlaysChart({ data }) {
  const mockedDates = data.filter((d) => d.mocked).map((d) => d.date)
  const mockedRange = mockedDates.length ? [mockedDates[0], mockedDates[mockedDates.length - 1]] : null

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          {mockedRange && (
            <ReferenceArea
              x1={mockedRange[0]}
              x2={mockedRange[1]}
              fill="#8b5cf6"
              fillOpacity={0.06}
              label={{ value: 'Mocked', position: 'insideTopLeft', fill: '#7c3aed', fontSize: 10 }}
            />
          )}
          <XAxis
            dataKey="date"
            tickFormatter={formatDay}
            stroke={CHART.axis}
            tick={{ fill: CHART.inkMuted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
            interval={4}
          />
          <YAxis
            stroke={CHART.axis}
            tick={{ fill: CHART.inkMuted, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            allowDecimals={false}
          />
          <Tooltip content={<ChartTooltip />} />
          <Legend wrapperStyle={{ fontSize: 12, color: CHART.inkSecondary }} />
          <Line
            type="monotone"
            dataKey="episodes_created"
            name="Episodes"
            stroke={SERIES.blue}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="plays"
            name="Plays"
            stroke={SERIES.orange}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="completions"
            name="Completions"
            stroke={SERIES.aqua}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
