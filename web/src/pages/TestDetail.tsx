/**
 * 单次测试详情页
 *
 * Tab:
 *   📊 报告(iframe 嵌 reports/task_X.html)
 *   💡 建议 + 自动应用
 *   🔄 跟其他测试对比
 */
import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react'

import { TestsAPI, TasksAPI, api } from '@/lib/api'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { MilestoneProgress } from '@/components/ui/Progress'


export default function TestDetail() {
  const { testId = '' } = useParams<{ testId: string }>()
  const { data: test, refetch } = useQuery({
    queryKey: ['test', testId],
    queryFn: () => TestsAPI.get(testId),
    enabled: !!testId,
    refetchInterval: (q) => q.state.data?.status === 'running' ? 5000 : false,
  })

  if (!test) return <div className="text-sm text-muted-foreground">加载中…</div>

  const passColor =
    test.pass_rate == null ? 'text-muted-foreground' :
    test.pass_rate >= 0.5 ? 'text-success' :
    test.pass_rate >= 0.2 ? 'text-warning' : 'text-destructive'

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to={`/tasks/${test.task_id}`}>
          <Button variant="ghost" size="md">
            <ArrowLeft size={14} /> {test.task_id}
          </Button>
        </Link>
        <h1 className="text-xl font-semibold font-mono">{testId}</h1>
        <Button variant="ghost" size="sm" onClick={() => refetch()}>
          <RefreshCw size={12} /> 刷新
        </Button>
      </div>

      {/* 元信息卡 */}
      <Card className="p-5">
        <div className="grid grid-cols-6 gap-6">
          <div>
            <div className="text-xs text-muted-foreground">状态</div>
            <div className="text-sm font-medium mt-1">
              {test.status === 'running' && <Badge variant="warning">⏳ 跑批中</Badge>}
              {test.status === 'done' && <Badge variant="success">✓ 完成</Badge>}
              {test.status === 'failed' && <Badge variant="danger">✗ 失败</Badge>}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">创建时间</div>
            <div className="text-sm mt-1 font-mono">{test.created_at.slice(0, 16)}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">agent 版本</div>
            <div className="text-sm mt-1 font-mono">{test.agent_version || '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">case 数</div>
            <div className="text-2xl font-semibold mt-1">{test.n_results}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">通过率</div>
            <div className={`text-2xl font-semibold mt-1 ${passColor}`}>
              {test.pass_rate == null ? '—' : `${(test.pass_rate * 100).toFixed(0)}%`}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">task_score 平均</div>
            <div className="text-2xl font-semibold mt-1">
              {test.task_score_avg == null ? '—' : test.task_score_avg.toFixed(3)}
            </div>
          </div>
        </div>
      </Card>

      {/* 进度 */}
      <Card className="px-5 py-4">
        <h2 className="text-sm font-semibold mb-3">进度</h2>
        <MilestoneProgress
          milestones={test.milestones}
          labels={['配置', '评测', '报告', '建议']}
        />
      </Card>

      {/* 参数 */}
      <details className="border border-border rounded-lg group">
        <summary className="px-5 py-3 cursor-pointer text-sm font-medium hover:bg-accent">
          📋 测试参数(只读)
        </summary>
        <div className="px-5 pb-4 border-t border-border pt-3">
          <pre className="text-xs font-mono bg-muted p-3 rounded-md overflow-auto">
            {JSON.stringify(test.params, null, 2)}
          </pre>
        </div>
      </details>

      {/* 3 Tab */}
      <Tabs taskId={test.task_id} testId={testId} />
    </div>
  )
}


function Tabs({ taskId, testId }: { taskId: string; testId: string }) {
  const [tab, setTab] = useState<'report' | 'rec' | 'compare'>('report')
  return (
    <div>
      <div className="flex gap-1 border-b border-border">
        {([
          ['report', '📊 报告'],
          ['rec', '💡 建议 + 自动应用'],
          ['compare', '🔄 对比其他测试'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key as any)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
                        ${tab === key ?
                          'border-foreground text-foreground' :
                          'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="pt-4">
        {tab === 'report' && <ReportTab taskId={taskId} />}
        {tab === 'rec' && <RecTab taskId={taskId} />}
        {tab === 'compare' && <CompareTab taskId={taskId} testId={testId} />}
      </div>
    </div>
  )
}


function ReportTab({ taskId }: { taskId: string }) {
  // 用 iframe 嵌入 FastAPI 自动 serve 的 reports/task_X.html
  // 注意:reports/ 当前只在前端 dev 模式下不能直接访问,需 FastAPI 加 static mount
  // 这里先做个 iframe + 提示
  const reportUrl = `http://localhost:8000/reports/task_${taskId}.html`
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          来自 reports/task_{taskId}.html(FastAPI 后端托管)
        </p>
        <a href={reportUrl} target="_blank" rel="noopener" className="text-xs text-foreground hover:underline">
          全屏新窗口 ↗
        </a>
      </div>
      <Card className="overflow-hidden p-0">
        <iframe
          src={reportUrl}
          title="dashboard"
          className="w-full"
          style={{ height: '900px', border: 'none' }}
        />
      </Card>
    </div>
  )
}


function RecTab({ taskId }: { taskId: string }) {
  const { data, refetch } = useQuery({
    queryKey: ['recommendations', taskId],
    queryFn: () => TasksAPI.recommendations(taskId),
  })

  if (!data?.recommendations?.length) {
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        还没有改进建议。在测试详情页或终端跑 <code>claw-eval recommend --task {taskId}</code>。
        <Button variant="outline" size="sm" className="ml-3" onClick={() => refetch()}>
          <RefreshCw size={12} /> 重新加载
        </Button>
      </Card>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        共 {data.recommendations.length} 条建议,按严重度排序。
      </p>
      {data.recommendations.map((r: any, i: number) => (
        <Card key={r.rubric_id} className="p-4 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">[{i + 1}]</span>
              <code className="text-sm font-mono font-semibold">{r.rubric_id}</code>
              <Badge variant="default">avg {r.avg_score?.toFixed(2)}</Badge>
              {r.estimated_lift && (
                <Badge variant="success">预期 +{r.estimated_lift.toFixed(2)}</Badge>
              )}
            </div>
          </div>
          {r.suggested_prompt_change && (
            <div className="text-sm whitespace-pre-wrap text-foreground/80 pl-6">
              {r.suggested_prompt_change}
            </div>
          )}
          {r.rationale && (
            <div className="text-xs text-muted-foreground pl-6">
              理由:{r.rationale}
            </div>
          )}
        </Card>
      ))}
    </div>
  )
}


function CompareTab({ taskId, testId }: { taskId: string; testId: string }) {
  const { data: tests = [] } = useQuery({
    queryKey: ['tests', taskId],
    queryFn: () => TestsAPI.listByTask(taskId),
  })
  const others = tests.filter(t => t.test_id !== testId)
  const [otherId, setOtherId] = useState<string>(others[0]?.test_id || '')

  if (others.length === 0) {
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        本任务还没有其他测试可对比。
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <span className="text-sm text-muted-foreground">跟哪个测试对比?</span>
        <select
          value={otherId}
          onChange={e => setOtherId(e.target.value)}
          className="px-2 py-1 border border-border rounded-md text-sm"
        >
          {others.map(t => (
            <option key={t.test_id} value={t.test_id}>{t.test_id}</option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">
          (对比功能下轮接 /api/regression 后端 → 当前看终端 `claw-eval regression`)
        </span>
      </div>
      <Card className="p-6 text-sm text-muted-foreground">
        回归对比报告(尚未接入 React,见 reports/regression_{taskId}.json)。
      </Card>
    </div>
  )
}
