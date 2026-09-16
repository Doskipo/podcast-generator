import { Pause, Play } from 'lucide-react'
import { useRef, useState } from 'react'

function formatTime(seconds) {
  if (!Number.isFinite(seconds)) return '0:00'
  const total = Math.floor(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

// The hero of an episode card: a large play control and a full-width
// progress bar, with the title/duration next to the button — replaces the
// earlier thin wrapper around the native <audio controls> element (whose
// browser-default transport can't be styled this way). See
// docs/decisions.md ("Episode card: hero player, collapsed sections").
export default function AudioPlayer({ src, title, durationSeconds, onPlay, onEnded }) {
  const audioRef = useRef(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  // Seeded from the episode's own known duration so the player doesn't
  // show "0:00" before the audio's metadata has loaded; onLoadedMetadata
  // below replaces it with the actual (more precise) value.
  const [duration, setDuration] = useState(durationSeconds || 0)

  function togglePlay() {
    const audio = audioRef.current
    if (!audio) return
    if (audio.paused) {
      audio.play()
    } else {
      audio.pause()
    }
  }

  function seek(e) {
    const value = Number(e.target.value)
    if (audioRef.current) audioRef.current.currentTime = value
    setCurrentTime(value)
  }

  return (
    <div className="rounded-lg border border-ink/10 bg-bg/[0.03] p-4">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={togglePlay}
          aria-label={playing ? 'Pause' : 'Play'}
          className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-accent text-surface shadow-md transition hover:bg-accent/90"
        >
          {playing ? (
            <Pause className="h-6 w-6" aria-hidden="true" />
          ) : (
            <Play className="h-6 w-6 translate-x-0.5" aria-hidden="true" />
          )}
        </button>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-ink">{title}</div>
          <div className="tabular-nums text-xs text-ink/55">
            {formatTime(currentTime)} / {formatTime(duration)}
          </div>
        </div>
      </div>

      <input
        type="range"
        min={0}
        max={duration || 0}
        step={0.1}
        value={currentTime}
        onChange={seek}
        aria-label="Seek"
        className="mt-3 w-full accent-accent"
      />

      {/* eslint-disable-next-line jsx-a11y/media-has-caption -- generated speech, no captions source */}
      <audio
        ref={audioRef}
        src={src}
        className="hidden"
        onPlay={() => {
          setPlaying(true)
          onPlay?.()
        }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          setPlaying(false)
          onEnded?.()
        }}
        onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
      />
    </div>
  )
}
