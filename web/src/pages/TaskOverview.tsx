import { lazy, Suspense, useState } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Plus, ArrowRight, CheckCircle2, Loader2 } from 'lucide-react'

import { TasksAPI, TestsAPI } from '@/lib/api'
import { JobStore } from '@/lib/jobs'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { MilestoneProgress } from '@/components/ui/Progress'
import { TaskConfig } from '@/components/TaskConfig'
import { ScriptList } from '@/components/ScriptList'
import { FlowGraph } from '@/components/FlowGraph'
import { AgentChatToggle } from '@/components/AgentChat'


const NewTestForm = lazy(() =>
  import('@/components/NewTestForm').then(module => ({
    default: module.NewTestForm,
  })),
)

export default function TaskOverview() {
  const { taskId = '' } = useParams<{ taskId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [showNew, setShowNew] = useState(false)

  const { data: task } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => TasksAPI.get(taskId),
    enabled: !!taskId,
  })
  const { data: tests = [] } = useQuery({
    queryKey: ['tests', taskId],
    queryFn: () => TestsAPI.listByTask(taskId),
    enabled: !!taskId,
    refetchInterval: false,
  })
  const { data: review } = useQuery({
    queryKey: ['review-status', taskId],
    queryFn: () => TasksAPI.reviewStatus(taskId),
    enabled: !!taskId,
  })
  const approveMut = useMutation({
    mutationFn: () => TasksAPI.approve(
      taskId,
      review?.rubrics_draft ?? false,
      review?.personas_pending ?? [],
    ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['review-status', taskId] })
      qc.invalidateQueries({ queryKey: ['task-rubrics', taskId] })
      qc.invalidateQueries({ queryKey: ['scripts', taskId] })
      qc.invalidateQueries({ queryKey: ['task', taskId] })
    },
  })

  const hasPendingReview = Boolean(
    review?.rubrics_draft || review?.personas_pending.length,
  )
  const canStartTest = Boolean(
    review?.rubrics_approved && review.personas_approved.length > 0,
  )

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/">
            <Button variant="ghost" size="md">
              <ArrowLeft size={14} /> 任务列表
            </Button>
          </Link>
          <h1 className="text-xl font-semibold font-mono">{taskId}</h1>
          {task?.description && (
            <span className="text-sm text-muted-foreground truncate max-w-xl">
              · {task.description}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Link to={`/tasks/${taskId}/meta-eval`}>
            <Button variant="ghost" size="sm">🎯 Meta-Eval 校准</Button>
          </Link>
          <AgentChatToggle taskId={taskId} />
        </div>
      </div>

      {/* 任务级数字 */}
      {task && (
        <div className="grid grid-cols-4 gap-3">
          <StatCard label="Rubric" value={task.n_rubrics} />
          <StatCard label="Persona" value={task.n_personas} extra={
            task.n_adv_personas > 0 ? `+${task.n_adv_personas} 对抗` : undefined
          } />
          <StatCard label="版本" value={`v${task.n_versions}`} />
          <StatCard label="测试" value={task.n_tests} />
        </div>
      )}

      {hasPendingReview && review && (
        <Card className="p-4 border-warning/40 bg-warning/5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="text-sm font-semibold">有配置草稿等待人工审核</div>
              <div className="text-xs text-muted-foreground mt-1">
                {review.rubrics_draft ? 'Rubrics 待审核' : 'Rubrics 已生效'}
                {' · '}
                {review.personas_pending.length > 0
                  ? `${review.personas_pending.length} 个剧本待审核：${review.personas_pending.join('、')}`
                  : '剧本均已生效'}
              </div>
              {approveMut.isError && (
                <div className="text-xs text-destructive mt-1">
                  审核失败：{(approveMut.error as any)?.response?.data?.detail || approveMut.error?.message || '请检查配置格式'}
                </div>
              )}
            </div>
            <Button
              variant="primary"
              size="sm"
              disabled={approveMut.isPending}
              onClick={() => {
                if (window.confirm('确认已检查这些 Rubrics 和剧本，并将其设为正式配置？')) {
                  approveMut.mutate()
                }
              }}
            >
              {approveMut.isPending
                ? <Loader2 size={13} className="animate-spin" />
                : <CheckCircle2 size={13} />}
              确认已审核并转正
            </Button>
          </div>
        </Card>
      )}

      {review && !canStartTest && !hasPendingReview && (
        <Card className="p-4 border-destructive/30 bg-destructive/5 text-sm">
          当前缺少正式 Rubrics 或正式剧本，补齐配置后才能启动测试。
        </Card>
      )}

      {/* 新建测试表单 / 测试历史 */}
      {showNew ? (
        <Card className="p-5">
          <Suspense fallback={<div className="py-12 text-center text-sm text-muted-foreground">加载测试配置…</div>}>
            <NewTestForm
              taskId={taskId}
              onCancel={() => setShowNew(false)}
              onStarted={(jobId, testId) => {
                JobStore.add({
                  jobId, type: 'test', taskId,
                  description: `测试 ${testId}`,
                  startedAt: Date.now(),
                })
                navigate(`/tests/${testId}`)
              }}
            />
          </Suspense>
        </Card>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold">测试历史</h2>
            <Button
              variant="primary"
              size="md"
              disabled={!review || !canStartTest}
              onClick={() => setShowNew(true)}
              title={!canStartTest ? '请先审核并转正 Rubrics 和至少一个剧本' : undefined}
            >
              <Plus size={14} /> 新建测试
            </Button>
          </div>

          <Card>
            {tests.length === 0 ? (
              <div className="px-5 py-12 text-center">
                <div className="text-sm text-muted-foreground mb-3">
                  还没有测试。
                </div>
                <Button
                  variant="primary"
                  disabled={!review || !canStartTest}
                  onClick={() => setShowNew(true)}
                  title={!canStartTest ? '请先审核并转正 Rubrics 和至少一个剧本' : undefined}
                >
                  <Plus size={14} /> 新建第一个测试
                </Button>
              </div>
            ) : (
              <div className="divide-y divide-border">
                {tests.map(t => (
                  <TestRow key={t.test_id} test={t} />
                ))}
              </div>
            )}
          </Card>
        </>
      )}

      {!showNew && (
        <>
          {/* 流程图 */}
          {task?.has_flow && (
            <details className="border border-border rounded-lg" open>
              <summary className="px-5 py-3 cursor-pointer text-sm font-medium hover:bg-accent">
                🔀 对话流程图
              </summary>
              <div className="px-5 py-4 border-t border-border">
                <FlowGraph taskId={taskId} />
              </div>
            </details>
          )}

          {/* 剧本列表 */}
          <details className="border border-border rounded-lg" open>
            <summary className="px-5 py-3 cursor-pointer text-sm font-medium hover:bg-accent">
              📜 模拟用户剧本
            </summary>
            <div className="px-5 py-4 border-t border-border">
              <ScriptList taskId={taskId} />
            </div>
          </details>

          {/* 任务级配置 */}
          <details className="border border-border rounded-lg">
            <summary className="px-5 py-3 cursor-pointer text-sm font-medium hover:bg-accent">
              ⚙️ 任务级配置(Prompt / Rubrics)
            </summary>
            <div className="px-5 py-4 border-t border-border">
              <TaskConfig taskId={taskId} />
            </div>
          </details>
        </>
      )}
    </div>
  )
}


function StatCard({ label, value, extra }: { label: string; value: number | string; extra?: string }) {
  return (
    <Card className="px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold mt-1 tracking-tight">
        {value}
        {extra && <span className="text-xs text-destructive ml-2 font-normal">{extra}</span>}
      </div>
    </Card>
  )
}


function TestRow({ test }: { test: any }) {
  const pr = test.pass_rate
  const passColor =
    pr == null ? 'text-muted-foreground' :
    pr >= 0.6 ? 'text-success' :
    pr >= 0.3 ? 'text-warning' : 'text-destructive'

  const statusBadge = {
    'running': <Badge variant="warning">⏳ 跑批中</Badge>,
    'done':    <Badge variant="success">✓ 完成</Badge>,
    'partial': <Badge variant="warning">⚠ 部分失败</Badge>,
    'failed':  <Badge variant="danger">✗ 失败</Badge>,
  }[test.status as string] ?? <Badge>{test.status}</Badge>

  return (
    <Link
      to={`/tests/${test.test_id}`}
      className="block px-5 py-3 hover:bg-accent/40 transition-colors group"
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-medium">{test.test_id}</span>
            {statusBadge}
            {test.agent_version && (
              <Badge variant="outline">
                <span className="font-mono text-[10px]">{test.agent_version}</span>
              </Badge>
            )}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            {test.created_at.slice(0, 16)} ·
            total={test.params?.total ?? '?'} ·
            {test.n_results} case
          </div>
          <div className="mt-2">
            <MilestoneProgress
              milestones={test.milestones}
              labels={['配置', '评测', '报告', '建议']}
            />
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={`text-2xl font-semibold ${passColor}`}>
            {pr == null ? '—' : `${Math.round(pr * 100)}%`}
          </div>
          <div className="text-xs text-muted-foreground">通过率</div>
        </div>
        <ArrowRight size={16} className="text-muted-foreground group-hover:text-foreground transition" />
      </div>
    </Link>
  )
}
