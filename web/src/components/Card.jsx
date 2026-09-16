export default function Card({ title, icon: Icon, actions, children, className = '' }) {
  return (
    <section className={`rounded-lg border border-ink/10 bg-surface p-4 shadow-sm ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && (
            <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
              {Icon && <Icon className="h-4 w-4 text-accent" aria-hidden="true" />}
              {title}
            </h2>
          )}
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}
