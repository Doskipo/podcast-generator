// A labeled range input with the current value shown alongside it. Used for
// interest weight (0-1) and the style knobs (humour 0-3, depth 1-3).
export default function Slider({ label, value, min, max, step = 1, onChange }) {
  return (
    <label className="block">
      <div className="mb-1 flex items-center justify-between text-sm">
        <span className="font-medium text-slate-700">{label}</span>
        <span className="tabular-nums text-slate-500">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-slate-900"
      />
    </label>
  )
}
