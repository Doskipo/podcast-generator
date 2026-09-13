import Button from './Button.jsx'
import Field, { TextInput } from './Field.jsx'

const PRESETS = [
  { label: 'Daily 7am', cron: '0 7 * * *' },
  { label: 'Daily 8am', cron: '0 8 * * *' },
  { label: 'Weekdays 7am', cron: '0 7 * * 1-5' },
  { label: 'Weekly, Monday 7am', cron: '0 7 * * 1' },
]

export default function SchedulePicker({ value, onChange }) {
  return (
    <div className="space-y-2">
      <Field label="Cron expression (min hour day-of-month month day-of-week)">
        <TextInput value={value} onChange={(e) => onChange(e.target.value)} />
      </Field>
      <div className="flex flex-wrap gap-1.5">
        {PRESETS.map((preset) => (
          <Button
            key={preset.cron}
            type="button"
            variant={value === preset.cron ? 'primary' : 'secondary'}
            onClick={() => onChange(preset.cron)}
          >
            {preset.label}
          </Button>
        ))}
      </div>
    </div>
  )
}
