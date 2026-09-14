export default function MockedBadge({ className = '' }) {
  return (
    <span className={`rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700 ${className}`}>
      Mocked
    </span>
  )
}
