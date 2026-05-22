import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { cn } from '@/lib/cn'

import TaskList from './pages/TaskList'
import TaskOverview from './pages/TaskOverview'
import TestDetail from './pages/TestDetail'
import Settings from './pages/Settings'
import Docs from './pages/Docs'


function NavItem({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'px-3 py-1.5 text-sm font-medium rounded-md',
          'transition-colors',
          isActive
            ? 'text-foreground bg-accent'
            : 'text-muted-foreground hover:text-foreground hover:bg-accent/60',
        )
      }
    >
      {children}
    </NavLink>
  )
}


function Layout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur border-b border-border">
        <div className="max-w-7xl mx-auto px-6 h-12 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <div className="flex items-center gap-2">
              <div className="w-6 h-6 rounded-md bg-primary text-primary-foreground
                              flex items-center justify-center font-bold text-xs">
                C
              </div>
              <span className="font-semibold text-sm">claw-eval</span>
            </div>
            <nav className="flex items-center gap-1">
              <NavItem to="/">任务</NavItem>
              <NavItem to="/settings">全局配置</NavItem>
              <NavItem to="/docs">使用文档</NavItem>
            </nav>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="http://localhost:8000/docs"
              target="_blank"
              rel="noopener"
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              API Docs ↗
            </a>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-6 py-6">{children}</main>
    </div>
  )
}


export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<TaskList />} />
        <Route path="/tasks/:taskId" element={<TaskOverview />} />
        <Route path="/tests/:testId" element={<TestDetail />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/docs" element={<Docs />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
