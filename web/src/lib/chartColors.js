// Charts use the exact same four-colour palette as the rest of the app
// (see index.css's @theme block) — no separate chart palette. Recharts'
// SVG fill/stroke attributes resolve CSS custom properties directly, so
// these are plain var() references, not a second copy of the hex values.
// Multiple series are told apart by opacity/dash, not by introducing new
// hues. See docs/ui.md ("Palette").

export const CHART = {
  bg: 'var(--color-bg)',
  surface: 'var(--color-surface)',
  ink: 'var(--color-ink)',
  accent: 'var(--color-accent)',
  grid: 'color-mix(in srgb, var(--color-ink) 12%, transparent)',
  axis: 'color-mix(in srgb, var(--color-ink) 35%, transparent)',
  inkMuted: 'color-mix(in srgb, var(--color-ink) 55%, transparent)',
}

// Categorical slots, in fixed order — same hue (accent) at decreasing
// opacity, plus ink for the last slot, rather than unrelated hues.
export const SERIES = {
  strong: 'var(--color-accent)',
  medium: 'color-mix(in srgb, var(--color-accent) 55%, transparent)',
  soft: 'var(--color-ink)',
}

// Provider identity: OpenAI is the accent, ElevenLabs is ink — consistent
// everywhere it appears (KPI copy, cost chart legend).
export const PROVIDER_COLOR = {
  openai: SERIES.strong,
  elevenlabs: SERIES.soft,
}
