import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART, SERIES } from '../../lib/chartColors.js'

export default function TopicDistributionChart({ data }) {
  return (
    <div style={{ height: Math.max(160, data.length * 36) }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
          <CartesianGrid stroke={CHART.grid} horizontal={false} />
          <XAxis
            type="number"
            allowDecimals={false}
            stroke={CHART.axis}
            tick={{ fill: CHART.inkMuted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
          />
          <YAxis
            type="category"
            dataKey="interest"
            width={140}
            stroke={CHART.axis}
            tick={{ fill: CHART.inkSecondary, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            formatter={(value) => [value, 'Episodes covering this']}
            contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: CHART.grid }}
          />
          <Bar dataKey="count" name="Episodes covering this" fill={SERIES.blue} radius={[0, 4, 4, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
