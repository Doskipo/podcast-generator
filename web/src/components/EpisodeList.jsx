import { ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import EpisodeCard from './EpisodeCard.jsx'

const OLDER_THAN_DAYS = 14
const OLDER_THAN_MS = OLDER_THAN_DAYS * 24 * 60 * 60 * 1000

// A list of episode cards, splitting out anything older than two weeks
// under its own "Older (N)" disclosure (closed by default). Used twice on
// the Episodes page — once for the default-visible episodes, once for the
// failed/no_content ones behind their own toggle — so each gets its own
// independent recent/older split.
export default function EpisodeList({ episodes, hosts }) {
  const [showOlder, setShowOlder] = useState(false)
  const cutoff = Date.now() - OLDER_THAN_MS
  const recent = episodes.filter((e) => new Date(e.created_at).getTime() >= cutoff)
  const older = episodes.filter((e) => new Date(e.created_at).getTime() < cutoff)

  return (
    <div className="space-y-3">
      {recent.map((episode) => (
        <EpisodeCard key={episode.episode_id} episode={episode} hosts={hosts} />
      ))}

      {older.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowOlder((v) => !v)}
            className="flex items-center gap-1.5 py-2 text-sm font-medium text-surface/70 hover:text-surface"
          >
            {showOlder ? <ChevronUp className="h-4 w-4" aria-hidden="true" /> : <ChevronDown className="h-4 w-4" aria-hidden="true" />}
            Older ({older.length})
          </button>
          {showOlder && (
            <div className="space-y-3">
              {older.map((episode) => (
                <EpisodeCard key={episode.episode_id} episode={episode} hosts={hosts} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
