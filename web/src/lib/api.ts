import axios from 'axios'
import {
  MOCK_TASKS, MOCK_TASK_DETAILS, MOCK_TESTS,
  MOCK_PERSONA_LIB, MOCK_RUBRICS, MOCK_RECOMMENDATIONS,
  isBackendAvailable,
} from './mockData'

export const api = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

// Demo 模式:无后端时返回 mock 数据
let _useMock: boolean | null = null

async function ensureMockCheck(): Promise<boolean> {
  if (_useMock !== null) return _useMock
  _useMock = !(await isBackendAvailable())
  return _useMock
}

async function withFallback<T>(real: () => Promise<T>, mock: () => T): Promise<T> {
  if (await ensureMockCheck()) return mock()
  try { return await real() }
  catch { return mock() }
}


// =================== Types ===================

export interface Milestones { m1: boolean; m2: boolean; m3: boolean; m4: boolean }

export interface TaskListItem {
  task_id: string
  description: string
  n_rubrics: number
  n_personas: number
  n_adv_personas: number
  n_versions: number
  n_tests: number
  last_pass_rate: number | null
  milestones: Milestones
}

export interface TaskDetail extends TaskListItem {
  prompt: string
  variables: Record<string, unknown>
  has_flow: boolean
}

export interface TestInfo {
  test_id: string
  task_id: string
  status: string
  created_at: string
  agent_version: string | null
  params: Record<string, any>
  n_results: number
  pass_rate: number | null
  task_score_avg: number | null
  milestones: Milestones
}

export interface NewTaskRequest {
  task_id: string
  description?: string
  prompt: string
}

export interface NewTestRequest {
  test_id?: string
  total: number
  no_judge?: boolean
  weights?: Record<string, number>
  dimensions?: Record<string, Record<string, number>>
  auto_recommend?: boolean
  prompt_version?: string | null
}

export interface PreviewResult {
  distribution: Record<string, Record<string, number>>
  samples: Array<Record<string, string>>
}

export interface JobStatus {
  job_id: string
  status: string
  message: string
}

export interface PersonaDimensionValue {
  value: string
  label: string
  desc: string
  usage_count: number
}

export interface PersonaDimension {
  dim: string
  label: string
  values: PersonaDimensionValue[]
}

export interface PersonaInfo {
  id: string
  is_adversarial: boolean
  personality_id: string
  covers_flow_nodes: string[]
  demographics: Record<string, string>
  max_rounds: number
  default_weight: number
}


// =================== Endpoints(全部带 mock fallback)===================

export const TasksAPI = {
  list: () => withFallback(
    () => api.get<TaskListItem[]>('/tasks').then(r => r.data),
    () => MOCK_TASKS,
  ),
  get: (id: string) => withFallback(
    () => api.get<TaskDetail>(`/tasks/${id}`).then(r => r.data),
    () => MOCK_TASK_DETAILS[id] || MOCK_TASKS[0] as TaskDetail,
  ),
  create: (req: NewTaskRequest) => withFallback(
    () => api.post<JobStatus>('/tasks', req).then(r => r.data),
    () => ({ job_id: 'mock', status: 'failed', message: 'Demo 模式不可新建任务' } as JobStatus),
  ),
  remove: (id: string) => withFallback(
    () => api.delete<{ deleted: string }>(`/tasks/${id}`).then(r => r.data),
    () => ({ deleted: id }),
  ),
  getPrompt: (id: string) => withFallback(
    () => api.get<{ prompt: string; variables: any; description: string }>(`/tasks/${id}/prompt`).then(r => r.data),
    () => {
      const t = MOCK_TASK_DETAILS[id] || MOCK_TASK_DETAILS[MOCK_TASKS[0].task_id]
      return { prompt: t.prompt, variables: t.variables, description: t.description }
    },
  ),
  updatePrompt: (id: string, prompt: string, description?: string) => withFallback(
    () => api.put(`/tasks/${id}/prompt`, { prompt, description }).then(r => r.data),
    () => ({ ok: true }),
  ),
  rubrics: (id: string) => withFallback(
    () => api.get<{ rubrics: any[]; is_draft: boolean }>(`/tasks/${id}/rubrics`).then(r => r.data),
    () => ({ rubrics: MOCK_RUBRICS, is_draft: false }),
  ),
  versions: (id: string) => withFallback(
    () => api.get<{ versions: any[] }>(`/tasks/${id}/versions`).then(r => r.data),
    () => ({ versions: [] }),
  ),
  personas: (id: string) => withFallback(
    () => api.get<{ personas: PersonaInfo[] }>(`/tasks/${id}/personas`).then(r => r.data),
    () => ({ personas: [] }),
  ),
  recommendations: (id: string) => withFallback(
    () => api.get<{ recommendations: any[]; generated_at: string }>(`/tasks/${id}/recommendations`).then(r => r.data),
    () => MOCK_RECOMMENDATIONS,
  ),
}

export const TestsAPI = {
  listByTask: (taskId: string) => withFallback(
    () => api.get<TestInfo[]>(`/tasks/${taskId}/tests`).then(r => r.data),
    () => MOCK_TESTS[taskId] || [],
  ),
  get: (testId: string) => withFallback(
    () => api.get<TestInfo>(`/tests/${testId}`).then(r => r.data),
    () => {
      for (const tests of Object.values(MOCK_TESTS)) {
        const t = tests.find(t => t.test_id === testId)
        if (t) return t
      }
      return MOCK_TESTS.meituan_rider[0]
    },
  ),
  start: (taskId: string, req: NewTestRequest) => withFallback(
    () => api.post<JobStatus>(`/tasks/${taskId}/tests`, req).then(r => r.data),
    () => ({ job_id: 'mock', status: 'failed', message: 'Demo 模式不可启动测试' } as JobStatus),
  ),
  previewPersonas: (taskId: string, dimensions: Record<string, Record<string, number>>, n = 30, _seed = 0) => withFallback(
    () => api.post<PreviewResult>(`/tasks/${taskId}/preview-personas`, { dimensions, n, seed: _seed }).then(r => r.data),
    () => {
      const dist: Record<string, Record<string, number>> = {}
      for (const [dim, weights] of Object.entries(dimensions)) {
        dist[dim] = {}
        const total = Object.values(weights).reduce((a, b) => a + b, 0)
        if (total > 0) {
          for (const [k, w] of Object.entries(weights)) {
            dist[dim][k] = Math.round(n * w / total)
          }
        }
      }
      const samples = Array.from({ length: Math.min(n, 10) }, (_, i) => {
        const s: Record<string, string> = {}
        for (const dim of Object.keys(dimensions)) {
          const keys = Object.keys(dimensions[dim])
          s[dim] = keys[i % keys.length] || 'unspecified'
        }
        return s
      })
      return { distribution: dist, samples } as PreviewResult
    },
  ),
}

export const PersonaLibAPI = {
  get: () => withFallback(
    () => api.get<{ dimensions: PersonaDimension[] }>('/persona-library').then(r => r.data),
    () => MOCK_PERSONA_LIB,
  ),
}

export const ConfigAPI = {
  getModels: () => withFallback(
    () => api.get('/config/models').then(r => r.data),
    () => ({
      sut: { model: 'mimo-v2.5', temperature: 0.7, reasoning_effort: 'low' },
      simulator: { model: 'mimo-v2-pro', temperature: 0.7, reasoning_effort: 'low' },
      judge: { model: 'mimo-v2.5-pro', temperature: 0.0, reasoning_effort: 'medium' },
      concurrency: 4,
    }),
  ),
  updateModels: (cfg: any) => withFallback(
    () => api.put('/config/models', cfg).then(r => r.data),
    () => ({ ok: true }),
  ),
  listKeys: () => withFallback(
    () => api.get<Record<string, string | null>>('/config/api-keys').then(r => r.data),
    () => ({ xiaomi_mimo: null, openai: null, anthropic: null }),
  ),
  saveKey: (provider: string, api_key: string) => withFallback(
    () => api.post('/config/api-key', { provider, api_key }).then(r => r.data),
    () => ({ ok: false, message: 'Demo 模式不可保存' }),
  ),
  testConnection: (provider: string, api_key?: string) => withFallback(
    () => api.post('/config/test-connection', { provider, api_key }).then(r => r.data),
    () => ({ ok: false, message: 'Demo 模式无后端,不可测连接' }),
  ),
}

export const JobsAPI = {
  get: (id: string) => api.get<JobStatus>(`/jobs/${id}`).then(r => r.data),
  getTest: (id: string) => api.get<JobStatus>(`/jobs/test/${id}`).then(r => r.data),
}
