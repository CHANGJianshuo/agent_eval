import axios from 'axios'

export const api = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

// =================== Types ===================

export interface Milestones {
  m1: boolean
  m2: boolean
  m3: boolean
  m4: boolean
}

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

// =================== Endpoints ===================

export const TasksAPI = {
  list: () => api.get<TaskListItem[]>('/tasks').then(r => r.data),
  get: (id: string) => api.get<TaskDetail>(`/tasks/${id}`).then(r => r.data),
  create: (req: NewTaskRequest) =>
    api.post<JobStatus>('/tasks', req).then(r => r.data),
  remove: (id: string) =>
    api.delete<{ deleted: string }>(`/tasks/${id}`).then(r => r.data),
  getPrompt: (id: string) =>
    api.get<{ prompt: string; variables: any; description: string }>(
      `/tasks/${id}/prompt`).then(r => r.data),
  updatePrompt: (id: string, prompt: string, description?: string) =>
    api.put(`/tasks/${id}/prompt`, { prompt, description }).then(r => r.data),
  rubrics: (id: string) =>
    api.get<{ rubrics: any[]; is_draft: boolean }>(
      `/tasks/${id}/rubrics`).then(r => r.data),
  versions: (id: string) =>
    api.get<{ versions: any[] }>(`/tasks/${id}/versions`).then(r => r.data),
  personas: (id: string) =>
    api.get<{ personas: PersonaInfo[] }>(
      `/tasks/${id}/personas`).then(r => r.data),
  recommendations: (id: string) =>
    api.get<{ recommendations: any[]; generated_at: string }>(
      `/tasks/${id}/recommendations`).then(r => r.data),
}

export const TestsAPI = {
  listByTask: (taskId: string) =>
    api.get<TestInfo[]>(`/tasks/${taskId}/tests`).then(r => r.data),
  get: (testId: string) =>
    api.get<TestInfo>(`/tests/${testId}`).then(r => r.data),
  start: (taskId: string, req: NewTestRequest) =>
    api.post<JobStatus>(`/tasks/${taskId}/tests`, req).then(r => r.data),
  previewPersonas: (taskId: string, dimensions: Record<string, Record<string, number>>, n = 30, seed = 0) =>
    api.post<PreviewResult>(`/tasks/${taskId}/preview-personas`, {
      dimensions, n, seed,
    }).then(r => r.data),
}

export const PersonaLibAPI = {
  get: () => api.get<{ dimensions: PersonaDimension[] }>(
    '/persona-library').then(r => r.data),
}

export const ConfigAPI = {
  getModels: () => api.get('/config/models').then(r => r.data),
  updateModels: (cfg: any) =>
    api.put('/config/models', cfg).then(r => r.data),
  listKeys: () =>
    api.get<Record<string, string | null>>('/config/api-keys').then(r => r.data),
  saveKey: (provider: string, api_key: string) =>
    api.post('/config/api-key', { provider, api_key }).then(r => r.data),
  testConnection: (provider: string, api_key?: string) =>
    api.post('/config/test-connection', { provider, api_key }).then(r => r.data),
}

export const JobsAPI = {
  get: (id: string) =>
    api.get<JobStatus>(`/jobs/${id}`).then(r => r.data),
  getTest: (id: string) =>
    api.get<JobStatus>(`/jobs/test/${id}`).then(r => r.data),
}
