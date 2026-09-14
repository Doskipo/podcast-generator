import MetricTooltip from './MetricTooltip.jsx'

export default function KpiCard({ label, value, why }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
        <MetricTooltip text={why} />
      </div>
      <div className="mt-1 text-2xl font-semibold text-slate-900">{value}</div>
    </div>
  )
}
