export default function ShowNotes({ items }) {
  if (!items?.length) return <p className="text-sm text-slate-500">No sources recorded yet.</p>

  return (
    <ul className="space-y-1">
      {items.map((item) => (
        <li key={item.url} className="text-sm">
          <a href={item.url} target="_blank" rel="noreferrer" className="text-slate-700 underline hover:text-slate-900">
            {item.title}
          </a>{' '}
          <span className="text-xs text-slate-400">({item.source})</span>
        </li>
      ))}
    </ul>
  )
}
