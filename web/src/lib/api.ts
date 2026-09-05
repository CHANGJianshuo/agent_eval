import axios from 'axios'
import {
  MOCK_TASKS, MOCK_TASK_DETAILS, MOCK_TESTS,
  MOCK_PERSONA_LIB, MOCK_RUBRICS, MOCK_RECOMMENDATIONS,
  MOCK_SCRIPTS,
} from './mockData'

export const isDemo = import.meta.env.MODE === 'gh-pages'

export const api = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

// 静态演示只提供下面显式列出的样本读取，其他请求不发往后端。
api.interceptors.request.use(config => {
  if (isDemo) throw new Error('Demo 模式仅供浏览，无法执行此操作')
  return config
})

async function withDemo<T>(real: () => Promise<T>, mock: () => T): Promise<T> {
  return isDemo ? mock() : real()
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
  seed?: number
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
  step?: number
  total_steps?: number
  step_label?: string
  task_id?: string
  job_type?: string
}

export interface FlowNode {
  id: string
  label: string
  rubric?: string
  optional?: boolean
}

export interface ScriptInfo {
  id: string
  filename: string
  name: string
  scenario: string
  is_adversarial: boolean
  is_draft: boolean
  probes: Array<{ id: string; inject_at_turn: number; text: string; description: string }>
  max_rounds: number
  covers_flow_nodes: string[]
  n_probes: number
  // v1 兼容
  personality: string
  states: Record<string, string>
  transitions: Record<string, string | Record<string, number>>
  initial_state: string
  n_states: number
}

export interface PersonaDimensionValue {
  value: string
  label: string
  desc: string
  description?: string       // attitude 维度:用户模拟器自我描述
  speaking_style?: string    // attitude 维度:说话风格
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


// =================== Endpoints ===================

export const TasksAPI = {
  list: () => withDemo(
    () => api.get<TaskListItem[]>('/tasks').then(r => r.data),
    () => MOCK_TASKS,
  ),
  get: (id: string) => withDemo(
    () => api.get<TaskDetail>(`/tasks/${id}`).then(r => r.data),
    () => MOCK_TASK_DETAILS[id] || MOCK_TASKS[0] as TaskDetail,
  ),
  create: (req: NewTaskRequest) =>
    api.post<JobStatus>('/tasks', req).then(r => r.data),
  remove: (id: string) =>
    api.delete<{ deleted: string }>(`/tasks/${id}`).then(r => r.data),
  getPrompt: (id: string) => withDemo(
    () => api.get<{ prompt: string; variables: any; description: string }>(`/tasks/${id}/prompt`).then(r => r.data),
    () => {
      const t = MOCK_TASK_DETAILS[id] || MOCK_TASK_DETAILS[MOCK_TASKS[0].task_id]
      return { prompt: t.prompt, variables: t.variables, description: t.description }
    },
  ),
  updatePrompt: (id: string, prompt: string, description?: string) =>
    api.put(`/tasks/${id}/prompt`, { prompt, description }).then(r => r.data),
  rubrics: (id: string) => withDemo(
    () => api.get<{ rubrics: any[]; is_draft: boolean }>(`/tasks/${id}/rubrics`).then(r => r.data),
    () => ({ rubrics: MOCK_RUBRICS, is_draft: false }),
  ),
  updateRubrics: (id: string, rubrics: any[], isDraft = false) =>
    api.put(`/tasks/${id}/rubrics`, { rubrics, is_draft: isDraft }).then(r => r.data),
  flow: (id: string) => withDemo(
    () => api.get<{ nodes: FlowNode[]; edges: string[][] }>(`/tasks/${id}/flow`).then(r => r.data),
    () => ({ nodes: [], edges: [] }),
  ),
  versions: (id: string) => withDemo(
    () => api.get<{ versions: any[] }>(`/tasks/${id}/versions`).then(r => r.data),
    () => ({ versions: [] }),
  ),
  personas: (id: string) => withDemo(
    () => api.get<{ personas: PersonaInfo[] }>(`/tasks/${id}/personas`).then(r => r.data),
    () => ({ personas: [] }),
  ),
  scripts: (id: string) => withDemo(
    () => api.get<{ scripts: ScriptInfo[] }>(`/tasks/${id}/scripts`).then(r => r.data),
    () => ({ scripts: MOCK_SCRIPTS[id] || [] }),
  ),
  reviewStatus: (id: string) => withDemo(
    () => api.get<{
      rubrics_approved: boolean; rubrics_draft: boolean;
      personas_approved: string[]; personas_pending: string[]
    }>(`/tasks/${id}/review-status`).then(r => r.data),
    () => ({
      rubrics_approved: true,
      rubrics_draft: false,
      personas_approved: (MOCK_SCRIPTS[id] || []).map(script => script.id),
      personas_pending: [],
    }),
  ),
  approve: (id: string, approveRubrics: boolean, approvePersonas: string[]) =>
    api.post(`/tasks/${id}/approve`, {
      approve_rubrics: approveRubrics, approve_personas: approvePersonas,
    }).then(r => r.data),
}

export const TestsAPI = {
  flow: (id: string) => withDemo(
    () => api.get<{ nodes: FlowNode[]; edges: [string, string][]; message?: string }>(`/tests/${id}/flow`).then(r => r.data),
    () => ({ nodes: [], edges: [] }),
  ),
  recommendations: (id: string) => withDemo(
    () => api.get<{ recommendations: any[]; generated_at: string | null }>(`/tests/${id}/recommendations`).then(r => r.data),
    () => MOCK_RECOMMENDATIONS,
  ),
  listByTask: (taskId: string) => withDemo(
    () => api.get<TestInfo[]>(`/tasks/${taskId}/tests`).then(r => r.data),
    () => MOCK_TESTS[taskId] || [],
  ),
  get: (testId: string) => withDemo(
    () => api.get<TestInfo>(`/tests/${testId}`).then(r => r.data),
    () => {
      for (const tests of Object.values(MOCK_TESTS)) {
        const t = tests.find(t => t.test_id === testId)
        if (t) return t
      }
      return MOCK_TESTS.meituan_rider[0]
    },
  ),
  results: (testId: string) => withDemo(
    () => api.get<{
      results: any[]
      scripts: string[]
      attitudes: string[]
      heatmap: Array<{ script: string; attitude: string; count: number; avg_score: number; passed: number }>
    }>(`/tests/${testId}/results`).then(r => r.data),
    () => ({ results: [], scripts: [], attitudes: [], heatmap: [] }),
  ),
  start: (taskId: string, req: NewTestRequest) =>
    api.post<JobStatus>(`/tasks/${taskId}/tests`, req).then(r => r.data),
  previewPersonas: (taskId: string, dimensions: Record<string, Record<string, number>>, n = 30, _seed = 0) => withDemo(
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
  get: () => withDemo(
    () => api.get<{ dimensions: PersonaDimension[] }>('/persona-library').then(r => r.data),
    () => MOCK_PERSONA_LIB,
  ),
  save: (dimensions: PersonaDimension[]) =>
    api.put('/persona-library', { dimensions }).then(r => r.data),
}

export const ConfigAPI = {
  getModels: () => withDemo(
    () => api.get('/config/models').then(r => r.data),
    () => ({
      sut: { model: 'deepseek-v4-flash', temperature: 0.7, reasoning_effort: 'low' },
      simulator: { model: 'deepseek-v4-flash', temperature: 0.7, reasoning_effort: 'low' },
      judge: { model: 'deepseek-v4-pro', temperature: 0.0, reasoning_effort: 'medium' },
      extract_rubric: { model: 'deepseek-v4-pro', temperature: 0.0, reasoning_effort: 'medium' },
      extract_personas: { model: 'deepseek-v4-pro', temperature: 0.0, reasoning_effort: 'medium' },
      extract_flow: { model: 'deepseek-v4-pro', temperature: 0.0, reasoning_effort: 'medium' },
      extract_variables: { model: 'deepseek-v4-pro', temperature: 0.0, reasoning_effort: 'low' },
      recommend: { model: 'deepseek-v4-pro', temperature: 0.0, reasoning_effort: 'medium' },
      apply_patch: { model: 'deepseek-v4-pro', temperature: 0.0, reasoning_effort: 'medium' },
      concurrency: 8,
    }),
  ),
  updateModels: (cfg: any) => api.put('/config/models', cfg).then(r => r.data),
  listKeys: () => withDemo(
    () => api.get<Record<string, string | null>>('/config/api-keys').then(r => r.data),
    () => ({ deepseek: null, xiaomi_mimo: null, openai: null, anthropic: null }),
  ),
  saveKey: (provider: string, api_key: string) =>
    api.post('/config/api-key', { provider, api_key }).then(r => r.data),
  testConnection: (provider: string, api_key?: string) =>
    api.post('/config/test-connection', { provider, api_key }).then(r => r.data),
}

export const JobsAPI = {
  list: () => api.get<JobStatus[]>('/jobs').then(r => r.data),
  get: (id: string) => api.get<JobStatus>(`/jobs/${id}`).then(r => r.data),
  getTestJob: (id: string) => api.get<JobStatus>(`/jobs/${id}`).then(r => r.data),
  cancel: (id: string) => api.post<JobStatus>(`/jobs/${id}/cancel`).then(r => r.data),
}
