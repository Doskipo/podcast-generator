// Interest weight as three discrete choices instead of a continuous slider
// — Low/Medium/High map to fixed 0.4/0.7/1.0, and the numeric value is what
// actually gets stored (podcast.models.Interest.weight is still a float).
// See docs/ui.md ("Interest weight").
const WEIGHT_CHOICES = [
  { label: 'Low', value: 0.4 },
  { label: 'Medium', value: 0.7 },
  { label: 'High', value: 1.0 },
]

export default function WeightChoice({ label, value, onChange }) {
  return (
    <div>
      <span className="mb-1 block text-sm font-medium text-ink/80">{label}</span>
      <div className="flex gap-1.5">
        {WEIGHT_CHOICES.map((choice) => (
          <button
            key={choice.label}
            type="button"
            onClick={() => onChange(choice.value)}
            aria-pressed={value === choice.value}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              value === choice.value ? 'bg-accent text-surface' : 'border border-ink/20 text-ink/70 hover:bg-ink/5'
            }`}
          >
            {choice.label}
          </button>
        ))}
      </div>
    </div>
  )
}
