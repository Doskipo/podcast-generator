// A one-line "why this matters" hint next to a metric label — CSS-only
// (group-hover / group-focus-within), no JS state, keyboard-reachable.
export default function MetricTooltip({ text }) {
  return (
    <span className="group relative ml-1 inline-flex">
      <span
        tabIndex={0}
        aria-label={text}
        className="inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full bg-ink/10 text-[9px] font-semibold leading-none text-ink/60 outline-none focus:ring-2 focus:ring-accent"
      >
        i
      </span>
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 w-56 -translate-x-1/2 rounded-md bg-ink px-2.5 py-1.5 text-xs font-normal normal-case leading-snug text-surface opacity-0 shadow-lg transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
      >
        {text}
      </span>
    </span>
  )
}
