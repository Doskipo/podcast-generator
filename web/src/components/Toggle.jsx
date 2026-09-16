export default function Toggle({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-2 text-sm font-medium text-ink/80">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-ink/20 accent-accent"
      />
      {label}
    </label>
  )
}
