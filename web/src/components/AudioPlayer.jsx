// A thin, palette-matched wrapper around the native <audio> element — the
// browser's own controls aren't restyleable beyond accent-color/colour-
// scheme (no standard CSS hooks for the scrubber/buttons), so this gives it
// a card-matched frame instead of building a custom transport. See
// docs/ui.md ("Audio player") for that trade-off.
export default function AudioPlayer({ src, onPlay, onEnded }) {
  return (
    <div className="rounded-lg border border-ink/10 bg-bg/[0.03] p-2">
      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- generated speech, no captions source */}
      <audio
        controls
        className="w-full accent-accent"
        style={{ colorScheme: 'light' }}
        src={src}
        onPlay={onPlay}
        onEnded={onEnded}
      />
    </div>
  )
}
