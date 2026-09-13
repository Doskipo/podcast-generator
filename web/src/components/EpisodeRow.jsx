import { useRef, useState } from 'react'
import { api } from '../api.js'
import ScriptView from './ScriptView.jsx'
import ShowNotes from './ShowNotes.jsx'
import StatusBadge from './StatusBadge.jsx'

export default function EpisodeRow({ episode }) {
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

  return (
    <div className="border-b border-slate-100 last:border-b-0">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center justify-between gap-3 px-1 py-3 text-left"
      >
        <div className="flex flex-col items-start gap-1">
          <div className="flex items-center gap-3">
            <span className="font-medium text-slate-900">{episode.episode_id}</span>
            <StatusBadge status={episode.status} />
            {episode.stage_reached && <span className="text-xs text-slate-400">stage: {episode.stage_reached}</span>}
          </div>
          {episode.status === 'no_content' && episode.no_content_interests?.length > 0 && (
            <p className="text-xs text-amber-700">
              No fresh content this run for: {episode.no_content_interests.join(', ')}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-400">
          {episode.total_characters != null && <span>{episode.total_characters} chars</span>}
          {episode.cost_estimate_usd != null && <span>${episode.cost_estimate_usd.toFixed(3)}</span>}
          <span>{expanded ? '▲' : '▼'}</span>
        </div>
      </button>

      {expanded && (
        <div className="space-y-4 border-t border-slate-100 px-1 py-4">
          {detailError && <p className="text-sm text-red-600">{detailError}</p>}
          {!detail && !detailError && <p className="text-sm text-slate-500">Loading…</p>}
          {detail && (
            <>
              {detail.status === 'done' && (
                // eslint-disable-next-line jsx-a11y/media-has-caption -- generated speech, no captions source
                <audio
                  controls
                  className="w-full"
                  src={api.episodeAudioUrl(episode.episode_id)}
                  onPlay={handlePlay}
                  onEnded={handleEnded}
                />
              )}
              {detail.status === 'no_content' && (
                <p className="text-sm text-amber-700">
                  No episode was generated — rank found zero usable articles this run
                  {detail.no_content_interests?.length > 0 && (
                    <>
                      {' '}
                      for: <span className="font-medium">{detail.no_content_interests.join(', ')}</span>
                    </>
                  )}
                  . This isn't an error — try again later, or widen that interest's window/queries in Settings.
                </p>
              )}
              {detail.status !== 'done' && detail.status !== 'no_content' && (
                <p className="text-sm text-slate-500">Audio not ready yet ({detail.status}).</p>
              )}

              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Show notes</h4>
                <ShowNotes items={detail.show_notes} />
              </div>

              <div>
                <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Script</h4>
                <ScriptView script={detail.script} />
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
