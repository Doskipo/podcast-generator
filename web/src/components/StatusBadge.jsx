import { AlertTriangle, CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react'

// Status is told apart by icon + weight, not by a dedicated colour per
// status — this app's palette is four colours total (see index.css), so
// "done" vs "running" vs "no_content" vs "failed" can't each get their own
// hue the way a slate/green/red/amber/blue badge set would. See docs/ui.md.
const CONFIG = {
  pending: { icon: Clock, className: 'bg-ink/5 text-ink/60' },
  running: { icon: Loader2, className: 'bg-accent/10 text-accent', iconClassName: 'animate-spin' },
  done: { icon: CheckCircle2, className: 'bg-accent/10 text-accent' },
  failed: { icon: XCircle, className: 'bg-ink/10 text-ink' },
  no_content: { icon: AlertTriangle, className: 'bg-ink/5 text-ink/70' },
}

export default function StatusBadge({ status }) {
  const { icon: Icon, className, iconClassName = '' } = CONFIG[status] ?? CONFIG.pending
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium capitalize ${className}`}>
      <Icon className={`h-3 w-3 ${iconClassName}`} aria-hidden="true" />
      {status.replace('_', ' ')}
    </span>
  )
}
