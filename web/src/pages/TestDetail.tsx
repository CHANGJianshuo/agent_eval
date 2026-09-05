/**
 * 单次测试详情页
 *
 * 包含:流程图 + 模拟用户分布 + 报告/建议/对比 Tab
 */
import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Loader2, RefreshCw } from 'lucide-react'

import { TestsAPI, TasksAPI, JobsAPI, api } from '@/lib/api'
import { JobStore } from '@/lib/jobs'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { MilestoneProgress } from '@/components/ui/Progress'
import { FlowGraph } from '@/components/FlowGraph'
import { RecTab, CompareTab, CoveragePanel, CaseViewer } from '@/components/EvaluationWorkflow'


export default function TestDetail() {
  const { testId = '' } = useParams<{ testId: string }>()
  const jobId = `test_${testId}`

  const { data: test, error: testError, refetch } = useQuery({
    queryKey: ['test', testId],
    queryFn: () => TestsAPI.get(testId),
    enabled: !!testId,
    refetchInterval: (q) => {
      const t = q.state.data
      if (!t) return 3000
      return ['prepared', 'running'].includes(t.status) ? 3000 : false
    },
  })

  const { data: jobData } = useQuery({
    queryKey: ['job-test', jobId],
    queryFn: () => JobsAPI.getTestJob(jobId).catch(() => null),
    enabled: !test || !!test.params.input_hash,
    refetchInterval: (q) => {
      const j = q.state.data
      if (!j) return false
      return ['running', 'canceling'].includes(j.status) ? 1500 : false
    },
  })

  const qc = useQueryClient()
  useEffect(() => {
    if (test && !['prepared', 'running'].includes(test.status)) {
      qc.invalidateQueries({ queryKey: ['test-results', testId] })
      qc.invalidateQueries({ queryKey: ['report-status', testId] })
      qc.invalidateQueries({ queryKey: ['coverage', testId] })
      qc.invalidateQueries({ queryKey: ['manifest', testId] })
    }
  }, [test?.status, testId, qc])

  useEffect(() => {
    if (jobData && !['running', 'canceling'].includes(jobData.status)) {
      qc.invalidateQueries({ queryKey: ['test', testId] })
      qc.invalidateQueries({ queryKey: ['test-results', testId] })
      qc.invalidateQueries({ queryKey: ['report-status', testId] })
      qc.invalidateQueries({ queryKey: ['recommendations', testId] })
    }
  }, [jobData?.status, testId, qc])

  const tracked = JobStore.getSnapshot().find(j => j.jobId === jobId)
  const taskIdFromJob = tracked?.taskId || ''

  if (testError) return <Card className="p-5 text-sm text-destructive">加载失败：{testError.message}<Button variant="ghost" onClick={() => refetch()}>重试</Button></Card>

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
              {['prepared', 'running'].includes(test.status) && <Badge variant="warning">⏳ 跑批中</Badge>}
              {test.status === 'done' && <Badge variant="success">✓ 完成</Badge>}
              {test.status === 'partial' && <Badge variant="warning">⚠ 部分失败</Badge>}
              {test.status === 'failed' && <Badge variant="danger">✗ 失败</Badge>}
              {test.status === 'canceled' && <Badge variant="warning">已取消</Badge>}
              {test.status === 'interrupted' && <Badge variant="warning">服务中断</Badge>}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">创建时间</div>
            <div className="text-sm mt-1 font-mono">{test.created_at.slice(0, 16)}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">输入快照</div>
            <div className="text-sm mt-1 font-mono">{test.params.input_hash ? test.agent_version : '未保存（历史运行）'}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">完整评分数</div>
            <div className="text-2xl font-semibold mt-1">{test.n_results}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">通过率（完整评分）</div>
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

      {!test.params.input_hash && <p className="text-xs text-muted-foreground">历史运行没有输入快照，历史评分尚未按新口径重新验证。</p>}

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
          <FlowGraph taskId={test.task_id} testId={testId} />
          <CoveragePanel testId={testId} />
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
  return '#b91c1c'
}

function scoreBg(score: number): string {
  if (score >= 0.6) return '#dcfce7'
  if (score >= 0.4) return '#fef9c3'
  if (score > 0) return '#fee2e2'
  return '#fee2e2'
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
  const scored = data.results.filter((r: any) => r.task_score != null && (!r.status || r.status === 'complete'))
  const evaluated = scored.length
  const avgScore = evaluated ? scored.reduce((sum: number, r: any) => sum + r.task_score, 0) / evaluated : 0
  const lookup = new Map(heatmap.map(h => [`${h.script}|${h.attitude}`, h]))

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted-foreground">
        {total} 个 case · 完整评分 {evaluated}/{total} · {scripts.length} 个剧本 × {attitudes.length} 种特点
      </div>
      {data?.results?.filter((r: any) => r.status !== 'complete').map((r: any) => <Card key={r.case_id} className="p-3 text-xs"><p>{r.case_id || r.persona_id}：{r.error_message || '评分未完成'}</p><CaseViewer testId={testId} caseId={r.case_id || ''}/></Card>)}

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
              const rowCount = rowCells.reduce((sum, c) => sum + (c?.count || 0), 0)
              const rowAvg = rowCount ? rowCells.reduce((sum, c) => sum + (c ? c.avg_score * c.count : 0), 0) / rowCount : 0
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
                    style={{ background: rowCount ? scoreBg(rowAvg) : undefined, color: rowCount ? scoreColor(rowAvg) : '#71717a' }}
                  >
                    {rowCount ? rowAvg.toFixed(2) : '—'}
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
                    style={{ background: colCount ? scoreBg(colAvg) : undefined, color: colCount ? scoreColor(colAvg) : '#71717a' }}
                  >
                    {colCount ? colAvg.toFixed(2) : '—'}
                    <span className="text-[9px] opacity-60 ml-0.5">×{colCount}</span>
                  </td>
                )
              })}
              <td
                className="px-2 py-1.5 text-center font-mono tabular-nums font-bold"
                style={{
                  background: evaluated ? scoreBg(avgScore) : undefined,
                  color: evaluated ? scoreColor(avgScore) : '#71717a',
                }}
              >
                {evaluated ? avgScore.toFixed(2) : '—'}
                <span className="text-[9px] opacity-60 ml-0.5">×{evaluated}</span>
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
          ['rec', '💡 本次建议'],
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
        {tab === 'rec' && <RecTab taskId={taskId} testId={testId} />}
        {tab === 'compare' && <CompareTab taskId={taskId} testId={testId} />}
      </div>
    </div>
  )
}


function ReportTab({ taskId, testId }: { taskId: string; testId: string }) {
  const [reportJob, setReportJob] = useState<string | null>(null)
  const { data: report, error, refetch } = useQuery({
    queryKey: ['report-status', testId],
    queryFn: () => api.get(`/tests/${testId}/report-status`).then(r => r.data),
    refetchInterval: reportJob ? 1500 : false,
  })
  const { data: job } = useQuery({
    queryKey: ['report-job', reportJob],
    queryFn: () => JobsAPI.get(reportJob!),
    enabled: !!reportJob,
    refetchInterval: q => q.state.data?.status === 'running' ? 1500 : false,
  })
  const generate = useMutation({
    mutationFn: () => api.post(`/tests/${testId}/report`).then(r => r.data),
    onSuccess: data => setReportJob(data.job_id),
  })
  useEffect(() => {
    if (job && job.status !== 'running') refetch()
  }, [job?.status])
  const generating = generate.isPending || (!!reportJob && (!job || job.status === 'running'))
  const reportUrl = report?.exists ? report.url : null
  const handleGenerate = () => generate.mutate()
  const failure = error?.message || generate.error?.message || (job && job.status !== 'done' && job.status !== 'running' ? job.message : '')
  if (failure) return <Card className="p-5 text-sm text-destructive">报告处理失败：{failure}<Button variant="ghost" onClick={() => { setReportJob(null); generate.reset(); refetch() }}>重试</Button></Card>
  if (!report) return <Loader2 className="animate-spin" size={18} />

  if (!reportUrl) {
    return (
      <Card className="px-5 py-12 text-center space-y-3">
        <div className="text-3xl">📊</div>
        <div className="text-sm font-medium">报告尚未生成或需要更新</div>
        <p className="text-xs text-muted-foreground max-w-md mx-auto">
          点击下方按钮，按当前统计口径重新生成本次运行的 HTML 报告。
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
