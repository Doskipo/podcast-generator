import { ChevronDown, ChevronUp } from 'lucide-react'
import { useRef, useState } from 'react'
import { api } from '../api.js'
import AudioPlayer from './AudioPlayer.jsx'
import ScriptView from './ScriptView.jsx'
import ShowNotes from './ShowNotes.jsx'
import StatusBadge from './StatusBadge.jsx'

function formatDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function formatDuration(seconds) {
  if (seconds == null) return null
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function EpisodeCard({ episode, hosts }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState(null)
  const [detailError, setDetailError] = useState(null)
  const hasFiredPlay = useRef(false)

  async function toggle() {
    const next = !expanded
    setExpanded(next)
    if (next && !detail) {
      try {
        setDetail(await api.getEpisode(episode.episode_id))
      } catch (err) {
        setDetailError(err.message)
      }
    }
  }

  function handlePlay() {
    if (hasFiredPlay.current) return
    hasFiredPlay.current = true
    api.postEpisodeEvent(episode.episode_id, 'played', {}).catch(() => {})
  }

  function handleEnded() {
    api.postEpisodeEvent(episode.episode_id, 'completed_playback', {}).catch(() => {})
  }

  const duration = formatDuration(episode.duration_s)

  return (
    <div className="rounded-lg border border-ink/10 bg-surface p-4 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-medium text-ink">{episode.title || episode.episode_id}</h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink/55">
            <span>{formatDate(episode.created_at)}</span>
            {duration && <span>{duration}</span>}
            <StatusBadge status={episode.status} />
          </div>
          {episode.status === 'no_content' && episode.no_content_interests?.length > 0 && (
            <p className="mt-1.5 text-xs text-ink/70">
              No fresh content this run for: {episode.no_content_interests.join(', ')}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={toggle}
          className="flex shrink-0 items-center gap-1 rounded-md border border-ink/20 px-2.5 py-1 text-xs font-medium text-ink/70 hover:bg-ink/5"
        >
          {expanded ? 'Hide' : 'Details'}
          {expanded ? <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" /> : <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />}
        </button>
      </div>

      {expanded && (
        <div className="mt-4 space-y-4 border-t border-ink/10 pt-4">
          {detailError && <p className="text-sm text-accent">{detailError}</p>}
          {!detail && !detailError && <p className="text-sm text-ink/55">Loading…</p>}
          {detail && (
            <>
              {detail.status === 'done' && (
                <AudioPlayer src={api.episodeAudioUrl(episode.episode_id)} onPlay={handlePlay} onEnded={handleEnded} />
              )}
              {detail.status === 'no_content' && (
                <p className="text-sm text-ink/70">
                  No episode was generated — rank found zero usable articles this run
                  {detail.no_content_interests?.length > 0 && (
                    <>
                      {' '}
                      for: <span className="font-medium text-ink">{detail.no_content_interests.join(', ')}</span>
                    </>
                  )}
                  . This isn't an error — try again later, or widen that interest's window/queries in Settings.
                </p>
              )}
              {detail.status !== 'done' && detail.status !== 'no_content' && (
                <p className="text-sm text-ink/55">Audio not ready yet ({detail.status}).</p>
              )}

              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink/40">Show notes</h4>
                <ShowNotes items={detail.show_notes} />
              </div>

              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink/40">Transcript</h4>
                <ScriptView script={detail.script} hosts={hosts} />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
