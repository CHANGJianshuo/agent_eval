import { useState, useEffect, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, ArrowRight, Loader2, Check } from 'lucide-react'

import { api, TasksAPI, JobsAPI, type NewTaskRequest, type TaskListItem } from '@/lib/api'
import { JobStore, type TrackedJob } from '@/lib/jobs'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { MilestoneProgress } from '@/components/ui/Progress'


export default function TaskList() {
  const [showNew, setShowNew] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const trackedJobs = useSyncExternalStore(JobStore.subscribe, JobStore.getSnapshot)
  const genJobs = trackedJobs.filter(j => j.type === 'generate')
  const testJobs = trackedJobs.filter(j => j.type === 'test')

  const { data: tasks = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['tasks'],
    queryFn: TasksAPI.list,
    refetchInterval: (genJobs.length + testJobs.length) > 0 ? 5000 : false,
  })
  const qc = useQueryClient()

  const removeMut = useMutation({
    mutationFn: async () => {
      for (const id of selected) await TasksAPI.remove(id)
    },
    onSuccess: () => setSelected(new Set()),
    onSettled: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
  })

  const toggle = (id: string) => {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  if (showNew) {
    return <NewTaskForm onCancel={() => setShowNew(false)} />
  }

  return (
    <div className="space-y-4">
      {/* Title row */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">任务</h1>
          <p className="text-[12px] text-muted-foreground mt-0.5">
            {tasks.length} 个任务 · 管理每个任务的多次测试
          </p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setShowNew(true)}>
          <Plus size={12} /> 新建任务
        </Button>
      </div>

      {/* Stats - compact */}
      <div className="grid grid-cols-4 gap-2">
        {[
          { label: '任务数', value: tasks.length },
          { label: '总测试', value: tasks.reduce((s, t) => s + t.n_tests, 0) },
          { label: 'Persona', value: tasks.reduce((s, t) => s + t.n_personas + t.n_adv_personas, 0) },
          { label: 'Rubric',  value: tasks.reduce((s, t) => s + t.n_rubrics, 0) },
        ].map(it => (
          <Card key={it.label} className="px-3 py-2">
            <div className="text-[11px] text-muted-foreground">{it.label}</div>
            <div className="text-lg font-semibold mt-0.5 tracking-tight">{it.value}</div>
          </Card>
        ))}
      </div>

      {/* 进行中的后台任务 */}
      {genJobs.map(job => (
        <RunningJobCard key={job.jobId} job={job} />
      ))}
      {testJobs.map(job => (
        <RunningJobCard key={job.jobId} job={job} />
      ))}

      {/* Toolbar */}
      {selected.size > 0 && (
        <div className="flex items-center justify-between px-3 py-1.5 rounded
                          border border-border bg-accent/40 text-[12px]">
          <span>已选 {selected.size} 个</span>
          <div className="flex items-center gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
              取消
            </Button>
            <Button variant="destructive" size="sm" disabled={removeMut.isPending} onClick={() => removeMut.mutate()}>
              <Trash2 size={11} /> 删除选中
            </Button>
          </div>
        </div>
      )}

      {/* Task list */}
      {removeMut.isError && (
        <div role="alert" className="text-sm text-destructive">
          删除失败：{(removeMut.error as any)?.response?.data?.detail || removeMut.error.message}
        </div>
      )}
      {isError ? (
        <Card className="p-8 text-center text-sm text-destructive" role="alert">
          任务加载失败，请检查后端服务。
          <Button variant="outline" size="sm" className="ml-3" onClick={() => refetch()}>
            重试
          </Button>
        </Card>
      ) : isLoading ? (
        <Card className="p-8 flex items-center justify-center text-muted-foreground text-sm">
          <Loader2 className="animate-spin mr-2" size={14} /> 加载中…
        </Card>
      ) : tasks.length === 0 && genJobs.length === 0 ? (
        <Card className="p-8 text-center">
          <div className="text-sm text-muted-foreground mb-3">
            还没有任务,点右上「新建任务」开始
          </div>
          <Button variant="primary" onClick={() => setShowNew(true)}>
            <Plus size={12} /> 新建任务
          </Button>
        </Card>
      ) : (
        <div className="space-y-1.5">
          {tasks.map(t => (
            <TaskRow
              key={t.task_id}
              task={t}
              selected={selected.has(t.task_id)}
              onToggle={() => toggle(t.task_id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}


// ==================== 进行中任务卡片 ====================

const GEN_STEPS = [
  { n: 1, label: '变量' },
  { n: 2, label: '流程图' },
  { n: 3, label: '评分项' },
  { n: 4, label: '剧本' },
]

function RunningJobCard({ job }: { job: TrackedJob }) {
  const qc = useQueryClient()
  const isTest = job.type === 'test'
  const { data: jobData, error: jobError } = useQuery({
    queryKey: ['job', job.jobId],
    queryFn: () => JobsAPI.get(job.jobId),
    refetchInterval: q => {
      const status = q.state.data?.status
      return !status || ['running', 'canceling'].includes(status) ? 2000 : false
    },
  })

  const isDone = jobData?.status === 'done'
  const isFailed = ['failed', 'partial', 'canceled', 'interrupted'].includes(jobData?.status || '')
  const cancelMut = useMutation({
    mutationFn: () => JobsAPI.cancel(job.jobId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['job', job.jobId] }),
  })
  const curStep = jobData?.step ?? 0
  const totalSteps = jobData?.total_steps ?? GEN_STEPS.length

  useEffect(() => {
    if (isDone || isFailed) {
      qc.invalidateQueries({ queryKey: ['tasks'] })
      if (isDone) {
        setTimeout(() => JobStore.remove(job.jobId), 3000)
      }
    }
  }, [isDone, isFailed])

  const steps = job.type === 'generate' ? GEN_STEPS : [{ n: 1, label: '测评中' }]

  return (
    <Card className={`px-4 py-3 space-y-2 ${
      isFailed ? 'border-destructive/30 bg-destructive/3' :
      isDone ? 'border-success/30 bg-success/3' :
      'border-warning/30 bg-warning/3'
    }`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          {!isDone && !isFailed && <Loader2 size={14} className="animate-spin" />}
          {isDone && <Check size={14} className="text-success" />}
          <span className="font-mono font-medium">{job.taskId}</span>
          {job.description && (
            <span className="text-muted-foreground">· {job.description}</span>
          )}
          <Badge variant={isDone ? 'success' : isFailed ? 'danger' : 'warning'}>
            {isDone ? '已完成' : isFailed ? ({ failed: '失败', partial: '部分完成', canceled: '已取消', interrupted: '服务中断' }[jobData?.status || 'failed']) : job.type === 'generate' ? '生成中' : '测评中'}
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          {!isDone && !isFailed && (
            <Button variant="ghost" size="sm" disabled={cancelMut.isPending || jobData?.status === 'canceling'} onClick={() => cancelMut.mutate()}>
              {jobData?.status === 'canceling' ? '取消中…' : '取消任务'}
            </Button>
          )}
          {(isDone || (isTest && isFailed)) && (
            <Link to={isTest ? `/tests/${job.jobId.replace('test_', '')}` : `/tasks/${job.taskId}`}>
              <Button variant="outline" size="sm">
                查看 <ArrowRight size={11} />
              </Button>
            </Link>
          )}
          {(isDone || isFailed) && (
            <Button variant="ghost" size="sm" onClick={() => JobStore.remove(job.jobId)}>
              关闭
            </Button>
          )}
        </div>
      </div>

      {(jobError || cancelMut.error) && <p className="text-xs text-destructive">{(jobError || cancelMut.error)?.message}</p>}
      {/* 进度条 */}
      {!isDone && !isFailed && (
        <>
          <div className="h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-foreground transition-all duration-500"
              style={{ width: `${(curStep / totalSteps) * 100}%` }}
            />
          </div>
          <div className="flex items-center gap-1 text-[11px]">
            {steps.map(s => {
              const done = curStep >= s.n
              const active = curStep === s.n
              return (
                <div key={s.n} className="flex items-center gap-1">
                  {s.n > 1 && <span className="text-muted-foreground/40 mx-0.5">›</span>}
                  {done ? (
                    <Check size={10} className="text-success" />
                  ) : active ? (
                    <Loader2 size={10} className="animate-spin" />
                  ) : (
                    <span className="w-2.5 h-2.5 rounded-full border border-border inline-block" />
                  )}
                  <span className={active ? 'font-medium' : done ? 'text-muted-foreground' : 'text-muted-foreground/50'}>
                    {s.label}
                  </span>
                </div>
              )
            })}
          </div>
        </>
      )}

      {isFailed && jobData?.message && (
        <div className="text-xs text-destructive truncate">
          {jobData.message.slice(-120)}
        </div>
      )}
    </Card>
  )
}


// ==================== 任务行 ====================

function TaskRow({ task, selected, onToggle }: {
  task: TaskListItem
  selected: boolean
  onToggle: () => void
}) {
  const passColor =
    task.last_pass_rate == null ? 'text-muted-foreground' :
    task.last_pass_rate >= 0.6  ? 'text-success' :
    task.last_pass_rate >= 0.3  ? 'text-warning' : 'text-destructive'

  return (
    <Card className="hover:border-foreground/20 transition-colors group">
      <div className="px-4 py-2.5 flex items-center gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          className="w-3.5 h-3.5 rounded border-border accent-foreground"
        />

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Link
              to={`/tasks/${task.task_id}`}
              className="font-mono text-[13px] font-medium hover:underline"
            >
              {task.task_id}
            </Link>
            {task.n_adv_personas > 0 && (
              <Badge variant="danger">{task.n_adv_personas} 对抗</Badge>
            )}
            {task.n_versions > 0 && (
              <Badge>v{task.n_versions}</Badge>
            )}
          </div>
          {task.description && (
            <div className="text-[11px] text-muted-foreground mt-0.5 truncate max-w-xl">
              {task.description}
            </div>
          )}
          <div className="mt-1.5">
            <MilestoneProgress milestones={task.milestones} />
          </div>
        </div>

        <div className="flex items-center gap-6 text-[12px]">
          <div className="text-right">
            <div className="text-muted-foreground text-[10px]">R / P</div>
            <div className="font-medium mt-0.5 tabular-nums">
              {task.n_rubrics}<span className="text-muted-foreground mx-1">·</span>{task.n_personas}
            </div>
          </div>
          <div className="text-right">
            <div className="text-muted-foreground text-[10px]">测试</div>
            <div className="font-medium mt-0.5 tabular-nums">{task.n_tests}</div>
          </div>
          <div className="text-right">
            <div className="text-muted-foreground text-[10px]">通过率</div>
            <div className={`text-base font-semibold mt-0.5 tabular-nums ${passColor}`}>
              {task.last_pass_rate == null ? '—' :
                `${Math.round(task.last_pass_rate * 100)}%`}
            </div>
          </div>
          <Link to={`/tasks/${task.task_id}`}>
            <Button variant="outline" size="sm">
              进入 <ArrowRight size={11} />
            </Button>
          </Link>
        </div>
      </div>
    </Card>
  )
}


// ==================== 新建任务表单 ====================

function NewTaskForm({ onCancel }: { onCancel: () => void }) {
  const [prompt, setPrompt] = useState('')
  const [generating, setGenerating] = useState(false)
  const [generatedMeta, setGeneratedMeta] = useState<{ taskId: string; description: string } | null>(null)
  const [error, setError] = useState('')


  const okPrompt = prompt.trim().length > 50
  const qc = useQueryClient()

  const handleGenerate = async () => {
    setGenerating(true)
    setError('')
    try {
      let taskId = 'new_task'
      let description = ''
      try {
        const meta = await api.post('/extract-task-meta', { prompt })
        taskId = meta.data.task_id || 'new_task'
        description = meta.data.description || ''
      } catch { /* fallback */ }
      setGeneratedMeta({ taskId, description })

      const r = await TasksAPI.create({ task_id: taskId, description, prompt })

      // 注册后台任务,然后返回列表
      JobStore.add({
        jobId: r.job_id,
        type: 'generate',
        taskId,
        description,
        startedAt: Date.now(),
      })
      qc.invalidateQueries({ queryKey: ['tasks'] })
      onCancel()
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || '创建失败')
      setGenerating(false)
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="md" onClick={onCancel}>
          ← 返回任务列表
        </Button>
        <h1 className="text-xl font-semibold tracking-tight">新建任务</h1>
      </div>

      <Card className="p-6 space-y-5">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">SUT System Prompt</label>
          <p className="text-xs text-muted-foreground">
            粘贴被测模型的完整指令。点击一键生成后，系统自动生成任务 ID、简介、评分项、剧本等。
          </p>
          <textarea
            autoFocus
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            disabled={generating}
            rows={14}
            placeholder={[
              '粘贴 SUT 的 System Prompt，如：',
              '',
              '你是XX平台的客服，负责致电用户通知功能升级。',
              '',
              '## 对话流程',
              '1. 问候确认身份',
              '2. 说明升级内容',
              '...',
              '',
              '## 约束',
              '- 每句话不超过 20 字',
              '- 不承诺优惠 / 不编造价格',
            ].join('\n')}
            className="w-full px-3 py-2 text-sm border border-border rounded-md
                        font-mono leading-relaxed
                        focus:outline-none focus:ring-2 focus:ring-foreground/20
                        disabled:opacity-60 disabled:cursor-not-allowed"
          />
          {prompt && !okPrompt && (
            <p className="text-xs text-destructive">内容太短（至少 50 字）</p>
          )}
        </div>

        {error && (
          <p className="text-sm text-destructive">{error}</p>
        )}

        <div className="flex items-center justify-end pt-2 border-t border-border gap-2">
          <Button variant="ghost" onClick={onCancel}>取消</Button>
          <Button
            variant="primary"
            disabled={!okPrompt || generating}
            onClick={handleGenerate}
          >
            {generating
              ? <><Loader2 size={14} className="animate-spin" /> 生成中…</>
              : <><Plus size={14} /> 一键生成</>}
          </Button>
        </div>
      </Card>
    </div>
  )
}
