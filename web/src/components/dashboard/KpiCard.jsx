import MetricTooltip from './MetricTooltip.jsx'

export default function KpiCard({ label, value, why }) {
  return (
    <div className="rounded-lg border border-ink/10 bg-surface p-4 shadow-sm">
      <div className="flex items-center text-xs font-medium uppercase tracking-wide text-ink/55">
        {label}
        <MetricTooltip text={why} />
      </div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
    </div>
  )
}
