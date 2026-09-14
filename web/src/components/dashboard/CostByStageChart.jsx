import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CHART, PROVIDER_COLOR } from '../../lib/chartColors.js'

const STAGE_LABEL = { rank: 'Rank', outline: 'Outline', script: 'Script', critique: 'Critique', tts: 'TTS' }
const PROVIDER_LABEL = { openai: 'OpenAI', elevenlabs: 'ElevenLabs' }

export default function CostByStageChart({ data }) {
  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis
            dataKey="stage"
            tickFormatter={(stage) => STAGE_LABEL[stage] ?? stage}
            stroke={CHART.axis}
            tick={{ fill: CHART.inkMuted, fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: CHART.axis }}
          />
          <YAxis
            stroke={CHART.axis}
            tick={{ fill: CHART.inkMuted, fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(value) => `$${value.toFixed(2)}`}
          />
          <Tooltip
            formatter={(value, name) => [`$${Number(value).toFixed(4)}`, PROVIDER_LABEL[name] ?? name]}
            labelFormatter={(stage) => STAGE_LABEL[stage] ?? stage}
            contentStyle={{ fontSize: 12, borderRadius: 6, borderColor: CHART.grid }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: CHART.inkSecondary }} formatter={(name) => PROVIDER_LABEL[name] ?? name} />
          <Bar dataKey="openai" name="openai" stackId="cost" fill={PROVIDER_COLOR.openai} isAnimationActive={false} />
          <Bar
            dataKey="elevenlabs"
            name="elevenlabs"
            stackId="cost"
            fill={PROVIDER_COLOR.elevenlabs}
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
