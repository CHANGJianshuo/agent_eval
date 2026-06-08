/**
 * 全局后台任务追踪 —— 生成/测评等长任务可在后台运行,任务列表显示进度。
 *
 * 用 zustand-like 的发布/订阅模式(无依赖),组件通过 useSyncExternalStore 订阅。
 * 持久化到 localStorage,页面刷新后自动恢复并向后端确认存活状态。
 */

import { api } from './api'

export interface TrackedJob {
  jobId: string
  type: 'generate' | 'test'
  taskId: string
  description?: string
  startedAt: number
}

const STORAGE_KEY = 'dial_tracked_jobs'

function loadFromStorage(): TrackedJob[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveToStorage(jobs: TrackedJob[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(jobs))
  } catch { /* quota exceeded — ignore */ }
}

let _jobs: TrackedJob[] = loadFromStorage()
const _listeners = new Set<() => void>()

function notify() {
  _listeners.forEach(fn => fn())
}

export const JobStore = {
  add(job: TrackedJob) {
    if (_jobs.some(j => j.jobId === job.jobId)) return
    _jobs = [..._jobs, job]
    saveToStorage(_jobs)
    notify()
  },
  remove(jobId: string) {
    _jobs = _jobs.filter(j => j.jobId !== jobId)
    saveToStorage(_jobs)
    notify()
  },
  getAll(): TrackedJob[] {
    return _jobs
  },
  subscribe(fn: () => void) {
    _listeners.add(fn)
    return () => { _listeners.delete(fn) }
  },
  getSnapshot(): TrackedJob[] {
    return _jobs
  },

  /** 页面加载时调用：向后端查询仍在运行的 job，合并到本地追踪列表 */
  async syncFromBackend() {
    try {
      const res = await api.get('/jobs')
      const backendJobs: Array<{
        job_id: string; status: string; task_id: string; job_type: string
      }> = res.data
      const activeBackend = backendJobs.filter(j => j.status === 'running')

      let changed = false
      for (const bj of activeBackend) {
        if (!_jobs.some(j => j.jobId === bj.job_id)) {
          _jobs = [..._jobs, {
            jobId: bj.job_id,
            type: (bj.job_type || 'generate') as 'generate' | 'test',
            taskId: bj.task_id,
            startedAt: Date.now(),
          }]
          changed = true
        }
      }

      // 清理已不存在于后端的 job（后端重启等情况）
      const backendIds = new Set(backendJobs.map(j => j.job_id))
      const before = _jobs.length
      _jobs = _jobs.filter(j => backendIds.has(j.jobId))
      if (_jobs.length !== before) changed = true

      if (changed) {
        saveToStorage(_jobs)
        notify()
      }
    } catch {
      // 后端不可达 — 保留本地缓存,等后续轮询自然清理
    }
  },
}
