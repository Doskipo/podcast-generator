import { NavLink, Route, Routes } from 'react-router-dom'
import Dashboard from './pages/Dashboard.jsx'
import Episodes from './pages/Episodes.jsx'
import Settings from './pages/Settings.jsx'

const navLinkClass = ({ isActive }) =>
  `px-3 py-2 rounded-md text-sm font-medium ${
    isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100'
  }`

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center gap-2 px-4 py-3">
          <span className="mr-4 font-semibold">Podcast Generator</span>
          <nav className="flex gap-1">
            <NavLink to="/settings" className={navLinkClass}>
              Settings
            </NavLink>
            <NavLink to="/episodes" className={navLinkClass}>
              Episodes
            </NavLink>
            <NavLink to="/dashboard" className={navLinkClass}>
              Dashboard
            </NavLink>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Episodes />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/episodes" element={<Episodes />} />
          <Route path="/dashboard" element={<Dashboard />} />
        </Routes>
      </main>
    </div>
  )
}
