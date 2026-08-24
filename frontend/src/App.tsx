import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import SetupPage from './pages/SetupPage'
import MonitorPage from './pages/MonitorPage'
import ReviewPage from './pages/ReviewPage'
import DashboardPage from './pages/DashboardPage'
import ExportPage from './pages/ExportPage'
import './App.css'

const qc = new QueryClient()

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <div className="shell">
          <header className="topbar">
            <div className="brand">Pharmaceutical Analog Uptake Workbench</div>
            <nav>
              <NavLink to="/">Setup</NavLink>
              <NavLink to="/monitor">Monitor</NavLink>
              <NavLink to="/dashboard">Dashboard</NavLink>
              <NavLink to="/export">Export</NavLink>
            </nav>
          </header>
          <main>
            <Routes>
              <Route path="/" element={<SetupPage />} />
              <Route path="/monitor" element={<MonitorPage />} />
              <Route path="/monitor/:runId" element={<MonitorPage />} />
              <Route path="/review/:jobId" element={<ReviewPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/dashboard/:runId" element={<DashboardPage />} />
              <Route path="/export" element={<ExportPage />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
