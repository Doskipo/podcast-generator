import StatusBadge from '../StatusBadge.jsx'
import MockedBadge from './MockedBadge.jsx'

export default function RecentFailuresTable({ failures }) {
  if (failures.length === 0) {
    return <p className="text-sm text-ink/55">No failures or no-content runs — every recent episode generated cleanly.</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="text-xs uppercase tracking-wide text-ink/40">
            <th className="py-1.5 pr-3 font-medium">Episode</th>
            <th className="py-1.5 pr-3 font-medium">Status</th>
            <th className="py-1.5 pr-3 font-medium">Stage</th>
            <th className="py-1.5 pr-3 font-medium">Reason</th>
            <th className="py-1.5 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {failures.map((failure) => (
            <tr key={failure.episode_id} className="border-t border-ink/8">
              <td className="py-1.5 pr-3 font-medium text-ink">
                {failure.episode_id}
                {failure.mocked && <MockedBadge className="ml-1.5" />}
              </td>
              <td className="py-1.5 pr-3">
                <StatusBadge status={failure.status} />
              </td>
              <td className="py-1.5 pr-3 text-ink/55">{failure.stage_reached ?? '—'}</td>
              <td className="py-1.5 pr-3 text-ink/70">{failure.reason ?? '—'}</td>
              <td className="py-1.5 whitespace-nowrap text-ink/40">{new Date(failure.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
