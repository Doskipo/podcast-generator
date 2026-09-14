// Chart palette — the validated default categorical order from the dataviz
// skill (references/palette.md), used verbatim (no brand substitution).
// Slot order is the CVD-safety mechanism, not cosmetic: keep the fixed
// order below rather than reassigning hues per chart. This app is
// light-mode only (see App.jsx), so only the light steps are needed.

export const CHART = {
  surface: '#fcfcfb',
  ink: '#0b0b0b',
  inkSecondary: '#52514e',
  inkMuted: '#898781',
  grid: '#e1e0d9',
  axis: '#c3c2b7',
}

// Categorical slots 1-3, in fixed order — never reassigned per-series.
export const SERIES = {
  blue: '#2a78d6',
  orange: '#eb6834',
  aqua: '#1baf7a',
}

// Provider identity is consistent everywhere it appears (KPI copy, cost
// chart legend): OpenAI is always blue, ElevenLabs is always orange.
export const PROVIDER_COLOR = {
  openai: SERIES.blue,
  elevenlabs: SERIES.orange,
}
