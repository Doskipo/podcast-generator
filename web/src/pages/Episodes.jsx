import { ChevronDown, ChevronUp } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import EpisodeList from '../components/EpisodeList.jsx'
import { EPISODE_CREATED_EVENT } from '../hooks/useGenerateEpisode.js'

const POLL_INTERVAL_MS = 3000
const IN_FLIGHT_STATUSES = new Set(['pending', 'running'])
// Default view: episodes that either worked or are still in flight.
// failed/no_content are real outcomes but not the common case — collapsed
// behind a toggle so a string of failures doesn't bury the episodes that
// actually generated. Mocked rows never reach this page at all — the
// backend excludes them (they exist only for the dashboard). See
// docs/decisions.md ("Episode list: mocked, status filter, resilience").
const DEFAULT_VISIBLE_STATUSES = new Set(['done', 'running', 'pending'])

export default function Episodes() {
  const [episodes, setEpisodes] = useState([])
  const [listError, setListError] = useState(null)
  const [hosts, setHosts] = useState([])
  const [showFailed, setShowFailed] = useState(false)
  const timeoutRef = useRef(null)

  // Re-fetches the list, then — only while at least one episode is still
  // pending/running — schedules itself again. See docs/decisions.md ("why
  // polling over websockets"): a few episodes, minutes-slow, single user —
  // a plain timeout loop is simpler than a websocket/push setup and more
  // than fast enough here.
  const refresh = useCallback(async () => {
    try {
      const list = await api.listEpisodes()
      setEpisodes(list)
      setListError(null)
      clearTimeout(timeoutRef.current)
      if (list.some((e) => IN_FLIGHT_STATUSES.has(e.status))) {
        timeoutRef.current = setTimeout(refresh, POLL_INTERVAL_MS)
      }
    } catch (err) {
      setListError(err.message)
    }
  }, [])

  useEffect(() => {
    refresh()
    window.addEventListener(EPISODE_CREATED_EVENT, refresh)
    return () => {
      clearTimeout(timeoutRef.current)
      window.removeEventListener(EPISODE_CREATED_EVENT, refresh)
    }
  }, [refresh])

  useEffect(() => {
    // Hosts (for the transcript's speaker colours/avatars/bios) come from
    // the single profile, not per-episode — best-effort: no profile yet
    // just means ScriptView falls back to uncoloured speaker names.
    api
      .getProfile()
      .then((p) => setHosts(p.podcast.hosts))
      .catch(() => setHosts([]))
  }, [])

  const visible = episodes.filter((e) => DEFAULT_VISIBLE_STATUSES.has(e.status))
  const hidden = episodes.filter((e) => !DEFAULT_VISIBLE_STATUSES.has(e.status))

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold text-surface">Episodes</h1>

      {listError && <p className="text-sm text-accent">{listError}</p>}

      {episodes.length === 0 && !listError ? (
        <p className="text-sm text-surface/70">No episodes yet — use the Generate now button to create your first one.</p>
      ) : (
        <div className="space-y-4">
          {visible.length > 0 ? (
            <EpisodeList episodes={visible} hosts={hosts} />
          ) : (
            <p className="text-sm text-surface/70">Nothing done, running, or pending yet.</p>
          )}

          {hidden.length > 0 && (
            <div>
              <button
                type="button"
                onClick={() => setShowFailed((v) => !v)}
                className="flex items-center gap-1.5 py-2 text-sm font-medium text-surface/70 hover:text-surface"
              >
                {showFailed ? <ChevronUp className="h-4 w-4" aria-hidden="true" /> : <ChevronDown className="h-4 w-4" aria-hidden="true" />}
                Show failed / no content ({hidden.length})
              </button>
              {showFailed && <EpisodeList episodes={hidden} hosts={hosts} />}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
