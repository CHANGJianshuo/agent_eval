/**
 * 单次测试详情页
 *
 * 包含:流程图 + 模拟用户分布 + 报告/建议/对比 Tab
 */
import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react'

import { TestsAPI, TasksAPI, JobsAPI, api } from '@/lib/api'
import { JobStore } from '@/lib/jobs'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { MilestoneProgress } from '@/components/ui/Progress'
import { FlowGraph } from '@/components/FlowGraph'


export default function TestDetail() {
  const { testId = '' } = useParams<{ testId: string }>()
  const jobId = `test_${testId}`

  const { data: test, refetch } = useQuery({
    queryKey: ['test', testId],
    queryFn: () => TestsAPI.get(testId).catch(() => null),
    enabled: !!testId,
    refetchInterval: (q) => {
      const t = q.state.data
      if (!t) return 3000
      return t.status === 'running' ? 5000 : false
    },
  })

  const { data: jobData } = useQuery({
    queryKey: ['job-test', jobId],
    queryFn: () => JobsAPI.getTestJob(jobId).catch(() => null),
    enabled: !test,
    refetchInterval: (q) => {
      const j = q.state.data
      if (!j) return false
      return j.status === 'running' ? 3000 : false
    },
  })

  const tracked = JobStore.getSnapshot().find(j => j.jobId === jobId)
  const taskIdFromJob = tracked?.taskId || ''

  if (!test && (!jobData || jobData.status === 'running')) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-3">
          {taskIdFromJob ? (
            <Link to={`/tasks/${taskIdFromJob}`}>
              <Button variant="ghost" size="md">
                <ArrowLeft size={14} /> {taskIdFromJob}
              </Button>
            </Link>
          ) : (
            <Link to="/">
              <Button variant="ghost" size="md">
                <ArrowLeft size={14} /> 返回
              </Button>
            </Link>
          )}
          <h1 className="text-xl font-semibold font-mono">{testId}</h1>
        </div>
        <Card className="px-5 py-12 text-center space-y-4">
          <Loader2 size={28} className="animate-spin mx-auto text-muted-foreground" />
          <div className="text-sm font-medium">测试跑批中…</div>
          <p className="text-xs text-muted-foreground max-w-md mx-auto">
            模拟用户正在与被测模型对话、评分器自动打分。完成后自动刷新显示报告。
          </p>
        </Card>
      </div>
    )
  }

  if (!test) return <div className="text-sm text-muted-foreground">加载中…</div>

  const passColor =
    test.pass_rate == null ? 'text-muted-foreground' :
    test.pass_rate >= 0.6 ? 'text-success' :
    test.pass_rate >= 0.3 ? 'text-warning' : 'text-destructive'

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

      {/* 流程图 + 模拟用户分布 并排 */}
      <div className="grid grid-cols-2 gap-4">
        <Card className="p-4">
          <h3 className="text-sm font-semibold mb-3">对话流程图</h3>
          <FlowGraph taskId={test.task_id} />
        </Card>
        <Card className="p-4">
          <h3 className="text-sm font-semibold mb-3">模拟用户分布</h3>
          <PersonaDistribution testId={testId} />
        </Card>
      </div>

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


function scoreColor(score: number): string {
  if (score >= 0.6) return '#166534'
  if (score >= 0.4) return '#a16207'
  if (score > 0) return '#b91c1c'
  return '#e5e7eb'
}

function scoreBg(score: number): string {
  if (score >= 0.6) return '#dcfce7'
  if (score >= 0.4) return '#fef9c3'
  if (score > 0) return '#fee2e2'
  return '#f4f4f5'
}

function PersonaDistribution({ testId }: { testId: string }) {
  const { data } = useQuery({
    queryKey: ['test-results', testId],
    queryFn: () => TestsAPI.results(testId),
  })

  if (!data || data.results.length === 0) {
    return (
      <div className="text-xs text-muted-foreground py-8 text-center">
        暂无数据（测试完成后自动显示）
      </div>
    )
  }

  const { scripts, attitudes, heatmap } = data
  const total = data.results.length
  const lookup = new Map(heatmap.map(h => [`${h.script}|${h.attitude}`, h]))

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted-foreground">
        {total} 个 case · {scripts.length} 个剧本 × {attitudes.length} 种特点
      </div>

      <div className="overflow-x-auto">
        <table className="text-xs border-collapse">
          <thead>
            <tr>
              <th className="text-left px-2 py-1.5 text-muted-foreground font-medium border-b border-border">
                剧本 \ 特点
              </th>
              {attitudes.map(att => (
                <th key={att} className="px-2 py-1.5 text-center text-muted-foreground font-medium border-b border-border min-w-[72px]">
                  {att}
                </th>
              ))}
              <th className="px-2 py-1.5 text-center text-muted-foreground font-medium border-b border-border min-w-[56px]">
                合计
              </th>
            </tr>
          </thead>
          <tbody>
            {scripts.map(sid => {
              const rowCells = attitudes.map(att => lookup.get(`${sid}|${att}`))
              const rowScores = rowCells.filter(Boolean).map(c => c!.avg_score)
              const rowAvg = rowScores.length > 0
                ? rowScores.reduce((a, b) => a + b, 0) / rowScores.length : 0
              const rowCount = rowCells.filter(Boolean).reduce((s, c) => s + c!.count, 0)
              return (
                <tr key={sid} className="border-b border-border/30">
                  <td className="px-2 py-1.5 font-mono font-medium whitespace-nowrap">
                    {sid}
                  </td>
                  {attitudes.map(att => {
                    const cell = lookup.get(`${sid}|${att}`)
                    if (!cell) {
                      return (
                        <td key={att} className="px-2 py-1.5 text-center text-muted-foreground/40">
                          —
                        </td>
                      )
                    }
                    return (
                      <td
                        key={att}
                        className="px-2 py-1.5 text-center font-mono tabular-nums"
                        style={{ background: scoreBg(cell.avg_score), color: scoreColor(cell.avg_score) }}
                        title={`${cell.count} case · passed ${cell.passed}/${cell.count}`}
                      >
                        {cell.avg_score.toFixed(2)}
                        <span className="text-[9px] opacity-60 ml-0.5">×{cell.count}</span>
                      </td>
                    )
                  })}
                  <td
                    className="px-2 py-1.5 text-center font-mono tabular-nums font-medium"
                    style={{ background: scoreBg(rowAvg), color: scoreColor(rowAvg) }}
                  >
                    {rowAvg.toFixed(2)}
                    <span className="text-[9px] opacity-60 ml-0.5">×{rowCount}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-border">
              <td className="px-2 py-1.5 font-medium text-muted-foreground">合计</td>
              {attitudes.map(att => {
                const colCells = scripts.map(sid => lookup.get(`${sid}|${att}`)).filter(Boolean) as typeof heatmap
                const colAvg = colCells.length > 0
                  ? colCells.reduce((s, c) => s + c.avg_score * c.count, 0) / colCells.reduce((s, c) => s + c.count, 0)
                  : 0
                const colCount = colCells.reduce((s, c) => s + c.count, 0)
                return (
                  <td
                    key={att}
                    className="px-2 py-1.5 text-center font-mono tabular-nums font-medium"
                    style={{ background: scoreBg(colAvg), color: scoreColor(colAvg) }}
                  >
                    {colAvg.toFixed(2)}
                    <span className="text-[9px] opacity-60 ml-0.5">×{colCount}</span>
                  </td>
                )
              })}
              <td
                className="px-2 py-1.5 text-center font-mono tabular-nums font-bold"
                style={{
                  background: scoreBg(total > 0 ? data.results.reduce((s: number, r: any) => s + (r.task_score || 0), 0) / total : 0),
                  color: scoreColor(total > 0 ? data.results.reduce((s: number, r: any) => s + (r.task_score || 0), 0) / total : 0),
                }}
              >
                {(total > 0 ? data.results.reduce((s: number, r: any) => s + (r.task_score || 0), 0) / total : 0).toFixed(2)}
                <span className="text-[9px] opacity-60 ml-0.5">×{total}</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
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
        {tab === 'report' && <ReportTab taskId={taskId} testId={testId} />}
        {tab === 'rec' && <RecTab taskId={taskId} />}
        {tab === 'compare' && <CompareTab taskId={taskId} testId={testId} />}
      </div>
    </div>
  )
}


function ReportTab({ taskId, testId }: { taskId: string; testId: string }) {
  const [reportUrl, setReportUrl] = useState<string | null>(null)
  const [checked, setChecked] = useState(false)
  const [generating, setGenerating] = useState(false)

  const checkReport = () => {
    api.get(`/tests/${testId}/report-status`)
      .then(r => {
        setReportUrl(r.data.exists ? r.data.url : null)
        setChecked(true)
      })
      .catch(() => setChecked(true))
  }

  useEffect(() => { checkReport() }, [testId])

  const handleGenerate = () => {
    setGenerating(true)
    api.post(`/tests/${testId}/report`)
      .then(() => {
        setTimeout(() => {
          checkReport()
          setGenerating(false)
        }, 3000)
      })
      .catch(() => setGenerating(false))
  }

  if (!checked) return null

  if (!reportUrl) {
    return (
      <Card className="px-5 py-12 text-center space-y-3">
        <div className="text-3xl">📊</div>
        <div className="text-sm font-medium">报告尚未生成</div>
        <p className="text-xs text-muted-foreground max-w-md mx-auto">
          测试跑批完成后，点击下方按钮生成 HTML 报告。
        </p>
        <Button
          variant="primary"
          disabled={generating}
          onClick={handleGenerate}
        >
          {generating ? (
            <><Loader2 size={14} className="animate-spin" /> 生成中…</>
          ) : '生成报告'}
        </Button>
      </Card>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {reportUrl}
        </p>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={handleGenerate} disabled={generating}>
            {generating ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            重新生成
          </Button>
          <a href={reportUrl} target="_blank" rel="noopener" className="text-xs text-foreground hover:underline">
            全屏新窗口 ↗
          </a>
        </div>
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
