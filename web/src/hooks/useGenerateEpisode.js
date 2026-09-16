import { useState } from 'react'
import { api } from '../api.js'

// "Generate now" lives in the app shell (App.jsx), not the Episodes page, so
// it's reachable from anywhere — but the Episodes list still needs to know
// a new episode was created so it can refresh/start polling. A plain
// window event is the simplest way to say that across routes without
// wiring up a context provider just for one signal.
export const EPISODE_CREATED_EVENT = 'podcast:episode-created'

export default function useGenerateEpisode() {
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState(null)

  async function generate() {
    setGenerating(true)
    setError(null)
    try {
      await api.createEpisode()
      window.dispatchEvent(new CustomEvent(EPISODE_CREATED_EVENT))
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  return { generating, error, generate }
}
