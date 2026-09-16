const VARIANTS = {
  primary: 'bg-accent text-surface hover:bg-accent/90 disabled:bg-ink/20',
  secondary: 'bg-surface text-ink border border-ink/20 hover:bg-ink/5 disabled:text-ink/40',
  danger: 'bg-surface text-ink border border-ink/20 hover:bg-ink/10 disabled:text-ink/40',
}

export default function Button({ variant = 'primary', className = '', ...props }) {
  return (
    <button
      {...props}
      className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
    />
  )
}
