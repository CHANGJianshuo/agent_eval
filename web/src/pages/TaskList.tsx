import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, ArrowRight, Loader2 } from 'lucide-react'

import { TasksAPI, type NewTaskRequest, type TaskListItem } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { MilestoneProgress } from '@/components/ui/Progress'


export default function TaskList() {
  const [showNew, setShowNew] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: TasksAPI.list,
  })
  const qc = useQueryClient()

  const removeMut = useMutation({
    mutationFn: (id: string) => TasksAPI.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
  })

  const toggle = (id: string) => {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  const removeSelected = async () => {
    for (const id of selected) await removeMut.mutateAsync(id)
    setSelected(new Set())
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

      {/* Toolbar */}
      {selected.size > 0 && (
        <div className="flex items-center justify-between px-3 py-1.5 rounded
                          border border-border bg-accent/40 text-[12px]">
          <span>已选 {selected.size} 个</span>
          <div className="flex items-center gap-1.5">
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
              取消
            </Button>
            <Button variant="destructive" size="sm" onClick={removeSelected}>
              <Trash2 size={11} /> 删除选中
            </Button>
          </div>
        </div>
      )}

      {/* Task list */}
      {isLoading ? (
        <Card className="p-8 flex items-center justify-center text-muted-foreground text-sm">
          <Loader2 className="animate-spin mr-2" size={14} /> 加载中…
        </Card>
      ) : tasks.length === 0 ? (
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


function TaskRow({ task, selected, onToggle }: {
  task: TaskListItem
  selected: boolean
  onToggle: () => void
}) {
  const passColor =
    task.last_pass_rate == null ? 'text-muted-foreground' :
    task.last_pass_rate >= 0.5  ? 'text-success' :
    task.last_pass_rate >= 0.2  ? 'text-warning' : 'text-destructive'

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


function NewTaskForm({ onCancel }: { onCancel: () => void }) {
  const [taskId, setTaskId] = useState('')
  const [description, setDescription] = useState('')
  const [prompt, setPrompt] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)

  const qc = useQueryClient()
  const createMut = useMutation({
    mutationFn: (req: NewTaskRequest) => TasksAPI.create(req),
    onSuccess: (r) => {
      setJobId(r.job_id)
      qc.invalidateQueries({ queryKey: ['tasks'] })
    },
  })

  const okId = /^[a-z][a-z0-9_]*$/.test(taskId)
  const okPrompt = prompt.trim().length > 50
  const ready = okId && okPrompt

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="md" onClick={onCancel}>
          ← 返回任务列表
        </Button>
        <h1 className="text-xl font-semibold tracking-tight">新建任务</h1>
      </div>

      <Card className="p-6 space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <label className="text-sm font-medium">任务 ID</label>
            <input
              value={taskId}
              onChange={e => setTaskId(e.target.value)}
              placeholder="如 live_upgrade_v2"
              className="w-full px-3 py-1.5 text-sm border border-border rounded-md
                          focus:outline-none focus:ring-2 focus:ring-foreground/20
                          font-mono"
            />
            {taskId && !okId && (
              <p className="text-xs text-destructive">必须英文小写下划线开头</p>
            )}
          </div>
          <div className="space-y-1">
            <label className="text-sm font-medium">简介(可选)</label>
            <input
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="如 课程平台直播升级通知"
              className="w-full px-3 py-1.5 text-sm border border-border rounded-md
                          focus:outline-none focus:ring-2 focus:ring-foreground/20"
            />
          </div>
        </div>

        <div className="space-y-1">
          <label className="text-sm font-medium">任务 Prompt(完整 SUT system prompt)</label>
          <textarea
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            rows={14}
            placeholder={`# Role: ...\n# Task: ...\n# Constraints:\n- ...\n# Conversation Flow:\n## Step 1: ...`}
            className="w-full px-3 py-2 text-sm border border-border rounded-md
                        font-mono focus:outline-none focus:ring-2 focus:ring-foreground/20"
          />
          {prompt && !okPrompt && (
            <p className="text-xs text-destructive">Prompt 太短(&lt;50 字)</p>
          )}
        </div>

        {createMut.isPending || jobId ? (
          <div className="rounded-md border border-border bg-muted px-3 py-2 text-sm">
            {jobId ? (
              <>
                ⏳ 后台跑 generate-task(~3-5 分钟)。
                <code className="text-xs ml-1">{jobId}</code>
                <div className="mt-2 text-xs text-muted-foreground">
                  几分钟后回到任务列表会看到新任务。
                </div>
              </>
            ) : (
              <span className="inline-flex items-center gap-2">
                <Loader2 size={14} className="animate-spin" />
                提交中…
              </span>
            )}
          </div>
        ) : null}

        <div className="flex items-center gap-2 pt-2">
          <Button variant="ghost" onClick={onCancel}>取消</Button>
          <Button
            variant="primary"
            disabled={!ready || createMut.isPending}
            onClick={() => createMut.mutate({
              task_id: taskId, description, prompt,
            })}
          >
            <Plus size={14} /> 一键生成
          </Button>
        </div>
      </Card>
    </div>
  )
}
