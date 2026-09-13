const STYLES = {
  pending: 'bg-slate-100 text-slate-600',
  running: 'bg-blue-100 text-blue-700 animate-pulse',
  done: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  // Not an error — rank genuinely found nothing to ground an episode in
  // (e.g. every interest's feeds/queries came up empty this run). Amber,
  // not red, on purpose.
  no_content: 'bg-amber-100 text-amber-700',
}

export default function StatusBadge({ status }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${STYLES[status] ?? STYLES.pending}`}>
      {status.replace('_', ' ')}
    </span>
  )
}
