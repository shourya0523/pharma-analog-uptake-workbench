import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import SetupPage from './pages/SetupPage'
import MonitorPage from './pages/MonitorPage'
import ReviewPage from './pages/ReviewPage'
import DashboardPage from './pages/DashboardPage'
import ObservabilityPage from './pages/ObservabilityPage'
import ExportPage from './pages/ExportPage'
import LibraryPage from './pages/LibraryPage'
import ProductDetailPage from './pages/ProductDetailPage'
import ReviewQueuePage from './pages/ReviewQueuePage'
import AddProductsPage from './pages/AddProductsPage'
import './App.css'

const qc = new QueryClient()

type Theme = 'light' | 'dark'
const THEME_KEY = 'wb-theme'

function currentTheme(): Theme {
  const set = document.documentElement.dataset.theme
  if (set === 'light' || set === 'dark') return set
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** Flips light/dark and remembers the choice; index.html applies it before first paint. */
function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme)
  const next: Theme = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      type="button"
      className="ghost theme-toggle"
      aria-label={`Switch to ${next} theme`}
      onClick={() => {
        document.documentElement.dataset.theme = next
        try {
          localStorage.setItem(THEME_KEY, next)
        } catch {
          // Storage can be blocked; the theme still applies for this visit.
        }
        setTheme(next)
      }}
    >
      {theme === 'dark' ? 'Light' : 'Dark'}
    </button>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <div className="shell">
          <header className="topbar">
            <div className="brand">Pharmaceutical Analog Uptake Workbench</div>
            <nav>
              <NavLink to="/" end>
                Library
              </NavLink>
              <NavLink to="/review">Review</NavLink>
              <NavLink to="/dashboard">Dashboard</NavLink>
              <NavLink to="/monitor">Monitor</NavLink>
              <NavLink to="/export">Export</NavLink>
              <NavLink to="/observability">Observability</NavLink>
            </nav>
            <ThemeToggle />
          </header>
          <main>
            <Routes>
              <Route path="/" element={<LibraryPage />} />
              <Route path="/add" element={<AddProductsPage />} />
              <Route path="/products/:productId" element={<ProductDetailPage />} />
              <Route path="/review" element={<ReviewQueuePage />} />
              <Route path="/setup" element={<SetupPage />} />
              <Route path="/monitor" element={<MonitorPage />} />
              <Route path="/monitor/:runId" element={<MonitorPage />} />
              <Route path="/review/:jobId" element={<ReviewPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/dashboard/:runId" element={<DashboardPage />} />
              <Route path="/observability" element={<ObservabilityPage />} />
              <Route path="/export" element={<ExportPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
