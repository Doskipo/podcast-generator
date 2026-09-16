// Thin fetch wrappers, one per backend endpoint. Same-origin paths in both
// dev (proxied by Vite, see vite.config.js) and prod (FastAPI serves this
// app), so no base URL/axios needed. Every endpoint lives under /api — see
// docs/decisions.md ("API routes under /api") for why: the bare path
// namespace (/episodes, /settings, ...) belongs to the SPA's own routes.
const API = '/api'

async function request(path, options) {
  const res = await fetch(path, options)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch {
      // response wasn't JSON — keep statusText
    }
    const error = new Error(`${res.status} ${detail}`)
    error.status = res.status
    throw error
  }
  if (res.status === 204) return null
  return res.json()
}

function getJson(path) {
  return request(path)
}

function putJson(path, body) {
  return request(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function postJson(path, body) {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
}

export const api = {
  getProfile: () => getJson(`${API}/profile`),
  putProfile: (profile) => putJson(`${API}/profile`, profile),
  getVoices: () => getJson(`${API}/voices`),
  suggestInterest: (topic, description) => postJson(`${API}/interests/suggest`, { topic, description }),

  listEpisodes: () => getJson(`${API}/episodes`),
  getEpisode: (id) => getJson(`${API}/episodes/${id}`),
  createEpisode: (until) => postJson(`${API}/episodes`, until ? { until } : {}),
  postEpisodeEvent: (id, type, metadata) => postJson(`${API}/episodes/${id}/events`, { type, metadata }),
  episodeAudioUrl: (id) => `${API}/episodes/${id}/audio`,

  getMetricsSummary: (includeMocked = false) => getJson(`${API}/metrics/summary?include_mocked=${includeMocked}`),
  getScheduleNext: () => getJson(`${API}/schedule/next`),
}
