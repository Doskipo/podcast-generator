// A labeled range input with the current value shown alongside it. Used for
// the style knobs (humour 0-3, depth 1-3) — interest weight now uses
// WeightChoice.jsx instead (see docs/ui.md, "Interest weight").
export default function Slider({ label, value, min, max, step = 1, onChange }) {
  return (
    <label className="block">
      <div className="mb-1 flex items-center justify-between text-sm">
        <span className="font-medium text-ink/80">{label}</span>
        <span className="tabular-nums text-ink/55">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-accent"
      />
    </label>
  )
}
