export default function MockedBadge({ className = '' }) {
  return (
    <span className={`rounded-full bg-accent/10 px-2 py-0.5 text-xs font-medium text-accent ${className}`}>
      Mocked
    </span>
  )
}
