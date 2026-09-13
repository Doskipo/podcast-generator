import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
import Button from '../components/Button.jsx'
import Card from '../components/Card.jsx'
import EpisodeRow from '../components/EpisodeRow.jsx'

const POLL_INTERVAL_MS = 3000
const IN_FLIGHT_STATUSES = new Set(['pending', 'running'])

export default function Episodes() {
  const [episodes, setEpisodes] = useState([])
  const [listError, setListError] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState(null)
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
    return () => clearTimeout(timeoutRef.current)
  }, [refresh])

  async function handleGenerate() {
    setGenerating(true)
    setGenerateError(null)
    try {
      await api.createEpisode()
      await refresh() // picks up the new pending row and (re)starts the poll loop
    } catch (err) {
      setGenerateError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  return (
    <Card
      title="Episodes"
      actions={
        <Button onClick={handleGenerate} disabled={generating}>
          {generating ? 'Starting…' : 'Generate now'}
        </Button>
      }
    >
      {generateError && <p className="mb-2 text-sm text-red-600">{generateError}</p>}
      {listError && <p className="mb-2 text-sm text-red-600">{listError}</p>}
      {episodes.length === 0 ? (
        <p className="text-sm text-slate-500">No episodes yet — click "Generate now" to create one.</p>
      ) : (
        <div>
          {episodes.map((episode) => (
            <EpisodeRow key={episode.episode_id} episode={episode} />
          ))}
        </div>
      )}
    </Card>
  )
}
