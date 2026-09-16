// Exactly two hosts always (PodcastSettings enforces it server-side), so
// two palette colours — accent and ink — are enough to tell speakers apart
// without introducing a third hue. `index` is the host's position in
// profile.podcast.hosts, the same order script.py assigns roles from.
const COLOR_BY_INDEX = ['bg-accent text-surface', 'bg-ink text-surface']

export function speakerColorClass(index) {
  return index === 0 ? 'text-accent' : 'text-ink'
}

export default function HostAvatar({ name, index, size = 'md' }) {
  const initial = name?.trim()?.[0]?.toUpperCase() ?? '?'
  const sizeClass = size === 'sm' ? 'h-5 w-5 text-[10px]' : 'h-8 w-8 text-xs'
  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center rounded-full font-semibold ${sizeClass} ${
        COLOR_BY_INDEX[index % COLOR_BY_INDEX.length]
      }`}
      aria-hidden="true"
    >
      {initial}
    </span>
  )
}
