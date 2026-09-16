// A labeled text/number input or textarea, sharing one visual style across
// the settings form.
export default function Field({ label, children, className = '' }) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-sm font-medium text-ink/80">{label}</span>
      {children}
    </label>
  )
}

const inputClass =
  'w-full rounded-md border border-ink/20 bg-surface px-3 py-1.5 text-sm text-ink shadow-sm focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent'

export function TextInput({ className = '', ...props }) {
  return <input {...props} className={`${inputClass} ${className}`} />
}

export function TextArea({ className = '', ...props }) {
  return <textarea {...props} className={`${inputClass} min-h-20 ${className}`} />
}

export function Select({ className = '', ...props }) {
  return <select {...props} className={`${inputClass} ${className}`} />
}
