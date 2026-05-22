import { useEffect, useState } from 'react'
import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { cn } from '@/lib/cn'

import TaskList from './pages/TaskList'
import TaskOverview from './pages/TaskOverview'
import TestDetail from './pages/TestDetail'
import Settings from './pages/Settings'
import Docs from './pages/Docs'
import { isBackendAvailable } from './lib/mockData'


function DemoBanner() {
  const [isDemo, setIsDemo] = useState<boolean | null>(null)
  useEffect(() => { isBackendAvailable().then(ok => setIsDemo(!ok)) }, [])
  if (isDemo !== true) return null
  return (
    <div className="bg-amber-50 border-b border-amber-200 px-6 py-2 text-xs">
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <div className="text-amber-900">
          🌐 <strong>Demo 模式</strong> · GitHub Pages 静态托管 · 数据是脱敏样本,「新建任务/启动测试」等操作不会执行
        </div>
        <a
          href="https://github.com/CHANGJianshuo/agent_eval"
          target="_blank" rel="noopener"
          className="text-amber-900 hover:underline font-medium ml-4"
        >
          完整源码 ↗
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
      <DemoBanner />
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
