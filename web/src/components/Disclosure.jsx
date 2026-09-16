import { ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'

// A labelled collapse/expand section — used for an episode card's
// Transcript/Sources/About the hosts, each collapsed by default so the
// player stays the card's hero. See docs/decisions.md ("Episode card: hero
// player, collapsed sections").
export default function Disclosure({ label, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink/40 hover:text-ink/70"
      >
        {open ? <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" /> : <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />}
        {label}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  )
}
