import { useEffect, useState } from 'react'
import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { cn } from '@/lib/cn'

import TaskList from './pages/TaskList'
import TaskOverview from './pages/TaskOverview'
import TestDetail from './pages/TestDetail'
import MetaEval from './pages/MetaEval'
import Settings from './pages/Settings'
import Docs from './pages/Docs'
import { isBackendAvailable } from './lib/mockData'
import { JobStore } from './lib/jobs'


function DemoBanner() {
  const [isDemo, setIsDemo] = useState<boolean | null>(null)
  useEffect(() => { isBackendAvailable().then(ok => setIsDemo(!ok)) }, [])
  if (isDemo !== true) return null
  return (
    <div className="bg-amber-50 border-b border-amber-200 px-5 py-1 text-[11px]">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <div className="text-amber-900">
          <strong>Demo</strong> · GitHub Pages 静态托管 · 样本数据 · 写操作不执行
        </div>
        <a
          href="https://github.com/CHANGJianshuo/agent_eval"
          target="_blank" rel="noopener"
          className="text-amber-900 hover:underline font-medium ml-4"
        >
          源码 ↗
        </a>
      </div>
    </div>
  )
}


function NavItem({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'px-2.5 py-1 text-[13px] font-medium rounded',
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
      <DemoBanner />
      <header className="sticky top-0 z-50 bg-background/85 backdrop-blur border-b border-border">
        <div className="max-w-7xl mx-auto px-5 h-10 flex items-center justify-between">
          <div className="flex items-center gap-5">
            <div className="flex items-center gap-1.5">
              <div className="w-5 h-5 rounded bg-primary text-primary-foreground
                              flex items-center justify-center font-bold text-[10px]">
                D
              </div>
              <span className="font-semibold text-[13px]">DialAgentEval</span>
            </div>
            <nav className="flex items-center gap-0.5">
              <NavItem to="/">任务</NavItem>
              <NavItem to="/settings">配置</NavItem>
              <NavItem to="/docs">文档</NavItem>
            </nav>
          </div>
          <div className="flex items-center gap-2">
            <a
              href="http://localhost:8000/docs"
              target="_blank"
              rel="noopener"
              className="text-[11px] text-muted-foreground hover:text-foreground"
            >
              API ↗
            </a>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-5 py-4">{children}</main>
    </div>
  )
}


export default function App() {
  useEffect(() => { JobStore.syncFromBackend() }, [])
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<TaskList />} />
        <Route path="/tasks/:taskId" element={<TaskOverview />} />
        <Route path="/tests/:testId" element={<TestDetail />} />
        <Route path="/tasks/:taskId/meta-eval" element={<MetaEval />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/docs" element={<Docs />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  )
}
