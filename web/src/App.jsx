import { LayoutDashboard, ListMusic, Settings as SettingsIcon } from 'lucide-react'
import { NavLink, Route, Routes } from 'react-router-dom'
import GenerateButton from './components/GenerateButton.jsx'
import Dashboard from './pages/Dashboard.jsx'
import Episodes from './pages/Episodes.jsx'
import Settings from './pages/Settings.jsx'

// One section per nav item, in this fixed order, shared by the desktop
// sidebar and the mobile bottom bar — see docs/ui.md ("Navigation").
const SECTIONS = [
  { to: '/episodes', label: 'Episodes', icon: ListMusic },
  { to: '/settings', label: 'Settings', icon: SettingsIcon },
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
]

function SidebarLink({ to, label, icon: Icon }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
          isActive ? 'bg-accent text-surface' : 'text-surface/60 hover:bg-surface/10 hover:text-surface'
        }`
      }
    >
      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
      {label}
    </NavLink>
  )
}

function BottomBarLink({ to, label, icon: Icon }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] font-medium ${
          isActive ? 'text-accent' : 'text-surface/50'
        }`
      }
    >
      <Icon className="h-5 w-5" aria-hidden="true" />
      {label}
    </NavLink>
  )
}

export default function App() {
  return (
    <div className="min-h-screen overflow-x-hidden bg-bg">
      {/* Desktop: left sidebar, one icon + label per section, always visible.
          See docs/ui.md ("Navigation"). */}
      <aside className="fixed inset-y-0 left-0 hidden w-56 flex-col gap-6 border-r border-surface/10 p-4 sm:flex">
        <span className="px-2 text-sm font-semibold tracking-wide text-surface">Podcast Generator</span>
        <GenerateButton />
        <nav className="flex flex-col gap-1">
          {SECTIONS.map((section) => (
            <SidebarLink key={section.to} {...section} />
          ))}
        </nav>
      </aside>

      {/* Mobile: bottom bar, plus a floating Generate button above it so the
          primary action stays reachable from every section. */}
      <div
        className="fixed right-4 z-20 sm:hidden"
        style={{ bottom: 'calc(64px + env(safe-area-inset-bottom, 0px) + 12px)' }}
      >
        <GenerateButton className="w-auto" compact />
      </div>
      <nav
        className="fixed inset-x-0 bottom-0 z-10 flex border-t border-surface/10 bg-bg sm:hidden"
        style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
      >
        {SECTIONS.map((section) => (
          <BottomBarLink key={section.to} {...section} />
        ))}
      </nav>

      <main className="min-h-screen px-4 py-4 pb-24 sm:ml-56 sm:px-8 sm:py-6 sm:pb-6">
        <div className="mx-auto max-w-4xl">
          <Routes>
            <Route path="/" element={<Episodes />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/episodes" element={<Episodes />} />
            <Route path="/dashboard" element={<Dashboard />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
