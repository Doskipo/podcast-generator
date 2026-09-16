import { AudioWaveform, Loader2 } from 'lucide-react'
import useGenerateEpisode from '../hooks/useGenerateEpisode.js'

// The one primary action in this app — always visible in the app shell
// (sidebar on desktop, a floating button above the bottom bar on mobile),
// not scoped to the Episodes page. See docs/ui.md ("Generate now").
export default function GenerateButton({ className = '', compact = false }) {
  const { generating, error, generate } = useGenerateEpisode()

  const icon = generating ? (
    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
  ) : (
    <AudioWaveform className="h-4 w-4" aria-hidden="true" />
  )

  return (
    <div className={className}>
      <button
        type="button"
        onClick={generate}
        disabled={generating}
        aria-label="Generate now"
        title="Generate now"
        className={`flex items-center justify-center gap-2 rounded-full bg-accent font-semibold text-surface shadow-lg shadow-accent/30 transition hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-70 ${
          compact ? 'h-14 w-14' : 'w-full px-4 py-2.5 text-sm'
        }`}
      >
        {icon}
        {!compact && (generating ? 'Starting…' : 'Generate now')}
      </button>
      {error && <p className={`mt-1.5 text-xs text-accent ${compact ? 'max-w-40 text-right' : ''}`}>{error}</p>}
    </div>
  )
}
