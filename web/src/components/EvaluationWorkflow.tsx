import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, TestsAPI } from '@/lib/api'
import { JobStore } from '@/lib/jobs'
import { Card } from './ui/Card'
import { Button } from './ui/Button'
import { ErrorLine } from './TaskConfig'

const input = 'w-full border border-border rounded px-2 py-1.5 text-sm bg-background'
function useJob(jobId: string, onFinished: () => void) {
  const finished = useRef('')
  const query = useQuery({
    queryKey: ['workflow-job', jobId],
    queryFn: () => api.get(`/jobs/${jobId}`).then((r) => r.data),
    enabled: !!jobId,
    refetchInterval: (q) =>
      !q.state.data || ['running', 'canceling'].includes(q.state.data.status) ? 1000 : false,
  })
  useEffect(() => {
    if (
      query.data &&
      !['running', 'canceling'].includes(query.data.status) &&
      finished.current !== jobId
    ) {
      finished.current = jobId
      onFinished()
    }
  }, [query.data?.status, jobId])
  return query
}

export function CaseViewer({
  testId,
  caseId,
  turn,
}: {
  testId: string
  caseId: string
  turn?: number
}) {
  const [open, setOpen] = useState(false)
  const query = useQuery({
    queryKey: ['case', testId, caseId],
    queryFn: () =>
      api.get(`/tests/${testId}/cases/${encodeURIComponent(caseId)}`).then((r) => r.data),
    enabled: open && !!caseId,
  })
  return (
    <div>
      <Button variant="ghost" size="sm" disabled={!caseId} onClick={() => setOpen(!open)}>
        {open ? '收起对话' : '查看证据对话'}
      </Button>
      {open && (
        <div className="border rounded p-3 max-h-80 overflow-auto space-y-2">
          <ErrorLine error={query.error} />
          {query.data?.result.error_message && (
            <p className="text-destructive">{query.data.result.error_message}</p>
          )}
          {query.data?.turns.map((t: any) => (
            <div
              key={t.turn}
              className={`text-xs p-2 rounded ${t.role === 'assistant' ? 'bg-blue-50' : 'bg-muted'} ${t.turn === turn ? 'ring-2 ring-amber-400' : ''}`}
            >
              <strong>
                #{t.turn} {t.role === 'assistant' ? '被测模型' : '模拟用户'}
              </strong>
              <p className="whitespace-pre-wrap">{t.text}</p>
            </div>
          ))}
          {query.data && !query.data.turns.length && (
            <p className="text-xs">未记录对话内容，请查看用例失败原因。</p>
          )}
        </div>
      )}
    </div>
  )
}

export function RecTab({ taskId, testId }: { taskId: string; testId: string }) {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: ['recommendations', testId],
    queryFn: () => TestsAPI.recommendations(testId) as Promise<any>,
  })
  const [jobId, setJobId] = useState('')
  const job = useJob(jobId, () => {
    qc.invalidateQueries({ queryKey: ['recommendations', testId] })
    qc.invalidateQueries({ queryKey: ['test', testId] })
  })
  const generate = useMutation({
    mutationFn: () => api.post(`/tests/${testId}/recommendations`).then((r) => r.data),
    onSuccess: (data) => setJobId(data.job_id),
  })
  const [candidateId, setCandidateId] = useState(
    () => localStorage.getItem(`eval-candidate:${testId}`) || '',
  )
  const patch = useMutation({
    mutationFn: (rubric: string) =>
      api
        .post(`/tests/${testId}/recommendations/${encodeURIComponent(rubric)}/patch`)
        .then((r) => r.data),
    onSuccess: (data) => {
      setCandidateId(data.candidate_id)
      localStorage.setItem(`eval-candidate:${testId}`, data.candidate_id)
    },
  })
  const candidate = useQuery({
    queryKey: ['candidate', testId, candidateId],
    queryFn: () => api.get(`/tests/${testId}/candidates/${candidateId}`).then((r) => r.data),
    enabled: !!candidateId,
    refetchInterval: (q) =>
      !q.state.data || ['running', 'canceling'].includes(q.state.data.status) ? 1000 : false,
  })
  const accept = useMutation({
    mutationFn: () =>
      api.post(`/tests/${testId}/candidates/${candidateId}/accept`, {
        expected_revision: candidate.data.base_revision,
        prompt: candidate.data.prompt,
      }),
    onSuccess: () => qc.invalidateQueries(),
  })
  const busy =
    generate.isPending ||
    (jobId && (!job.data || ['running', 'canceling'].includes(job.data.status)))
  const state = query.data?.status || 'not_generated'
  const labels: Record<string, string> = {
    not_generated: '尚未生成建议',
    no_weakness: '没有满足筛选条件的薄弱项（至少 3 次评分且平均分低于 0.8）',
    analysis_only: '当前只有薄弱项统计，尚未生成修改建议',
    failed: '建议生成失败，可重试',
    partial: '部分建议生成失败，请检查各项错误',
    completed: '建议已生成，修改效果需要回归验证',
  }
  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-center">
        <Button variant="primary" disabled={!!busy} onClick={() => generate.mutate()}>
          {busy ? '生成中…' : '生成 / 重试本次建议'}
        </Button>
        <span className="text-sm text-muted-foreground">{labels[state]}</span>
      </div>
      <ErrorLine
        error={
          query.error ||
          generate.error ||
          patch.error ||
          accept.error ||
          candidate.error ||
          job.error
        }
      />
      {job.data && ['failed', 'partial', 'interrupted'].includes(job.data.status) && (
        <ErrorLine error={job.data.log || job.data.message || '后台操作失败，请重试'} />
      )}
      {query.data?.recommendations?.map((rec: any) => (
        <Card key={rec.rubric_id} className="p-4 space-y-3">
          <div className="flex justify-between">
            <strong>{rec.rubric_id}</strong>
            <span className="text-xs">
              平均分 {rec.avg_score?.toFixed(2)} · {rec.n_triggered} 次评分
            </span>
          </div>
          <ErrorLine error={rec.llm_error} />
          <p className="text-sm whitespace-pre-wrap">{rec.suggested_prompt_change}</p>
          <p className="text-xs text-muted-foreground">{rec.rationale}</p>
          {rec.violation_samples?.map((sample: any, i: number) => (
            <div key={i} className="border-l pl-3 space-y-1 text-xs">
              <p>
                {sample.case} · 第 {sample.turn ?? '未知'} 轮 · {sample.reasoning}
              </p>
              <blockquote className="whitespace-pre-wrap">{sample.evidence}</blockquote>
              <CaseViewer testId={testId} caseId={sample.case_id || ''} turn={sample.turn} />
            </div>
          ))}
          <Button
            variant="outline"
            disabled={
              !rec.suggested_prompt_change ||
              patch.isPending ||
              candidate.data?.status === 'running'
            }
            onClick={() => patch.mutate(rec.rubric_id)}
          >
            生成候选修改并预览差异
          </Button>
        </Card>
      ))}
      {candidateId && (
        <Card className="p-4 space-y-3">
          <h3 className="font-medium">候选修改</h3>
          {['running', 'canceling'].includes(candidate.data?.status || 'running') && (
            <p>正在生成候选 Prompt…</p>
          )}
          <ErrorLine error={candidate.data?.error} />
          {candidate.data?.prompt && (
            <>
              <pre className="max-h-80 overflow-auto bg-muted p-3 text-xs whitespace-pre-wrap">
                {candidate.data.diff || '没有文本变化'}
              </pre>
              <details>
                <summary className="text-sm cursor-pointer">完整候选 Prompt</summary>
                <pre className="text-xs whitespace-pre-wrap mt-2">{candidate.data.prompt}</pre>
              </details>
              {candidate.data.status === 'draft' ? (
                <Button
                  variant="primary"
                  disabled={accept.isPending || !candidate.data.diff}
                  onClick={() => accept.mutate()}
                >
                  采纳并保存新版本
                </Button>
              ) : (
                <p className="text-sm">已保存版本：{candidate.data.accepted_version}</p>
              )}
            </>
          )}
        </Card>
      )}
      <CandidateRun taskId={taskId} testId={testId} />
    </div>
  )
}

export function CandidateRun({ taskId, testId }: { taskId: string; testId: string }) {
  const navigate = useNavigate()
  const start = useMutation({
    mutationFn: async () => {
      const config = await api.get(`/tasks/${taskId}/configuration`)
      return (
        await api.post(`/tests/${testId}/candidate-test`, {
          expected_revision: config.data.revision,
        })
      ).data
    },
    onSuccess: (data) => {
      JobStore.add({ jobId: data.job_id, taskId, type: 'test', startedAt: Date.now() })
      navigate(`/tests/${data.test_id}`)
    },
  })
  return (
    <Card className="p-4 space-y-3">
      <p className="text-sm">
        将当前已保存的 Prompt
        放到本次运行的固定用例中复测。业务变量、评分标准、Judge、模拟配置沿用基准快照。
      </p>
      <Button variant="primary" disabled={start.isPending} onClick={() => start.mutate()}>
        {start.isPending ? '提交中…' : '用固定基准复测当前 Prompt'}
      </Button>
      <ErrorLine error={start.error} />
    </Card>
  )
}

export function CompareTab({ taskId, testId }: { taskId: string; testId: string }) {
  const tests = useQuery({
    queryKey: ['tests', taskId],
    queryFn: () => TestsAPI.listByTask(taskId),
  })
  const current = useQuery({ queryKey: ['test', testId], queryFn: () => TestsAPI.get(testId) })
  const manifest = useQuery({
    queryKey: ['manifest', testId],
    queryFn: () => api.get(`/tests/${testId}/manifest`).then((r) => r.data),
  })
  const [selected, setSelected] = useState('')
  const old = selected || manifest.data?.candidate_of || ''
  const [threshold, setThreshold] = useState(0.05)
  const compare = useQuery({
    queryKey: ['regression', old, testId, threshold],
    queryFn: () =>
      api.get('/regression', { params: { old, new: testId, threshold } }).then((r) => r.data),
    enabled:
      !!old &&
      threshold > 0 &&
      threshold <= 1 &&
      !!current.data &&
      !['prepared', 'running'].includes(current.data.status),
  })
  const rep = compare.data
  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-center">
        <label className="text-sm flex-1">
          基准运行
          <select
            aria-label="回归基准"
            className={input}
            value={old}
            onChange={(e) => setSelected(e.target.value)}
          >
            <option value="">选择基准运行</option>
            {tests.data
              ?.filter((t) => t.test_id !== testId)
              .map((t) => (
                <option key={t.test_id} value={t.test_id}>
                  {t.test_id}
                </option>
              ))}
          </select>
        </label>
        <label className="text-sm w-32">
          变化幅度阈值
          <input
            aria-label="回归阈值"
            className={input}
            type="number"
            min={0.001}
            max={1}
            step={0.01}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
          />
        </label>
      </div>
      <p className="text-xs text-muted-foreground">
        比较本次运行与所选基准。阈值用于阻断退化，不代表统计显著性；任一相同用例出现新的安全违规都会阻断。
      </p>
      <ErrorLine error={tests.error || compare.error} />
      {compare.isFetching && <p className="text-sm">正在核对用例和评测条件…</p>}
      {rep && (
        <Card className="p-4 space-y-3">
          <p className={`font-medium ${rep.gate_passed ? 'text-success' : 'text-destructive'}`}>
            {!rep.comparable
              ? '评测条件不满足，无法给出回归结论'
              : rep.gate_passed
                ? '回归检查通过'
                : '回归检查未通过'}
          </p>
          {[...(rep.issues || []), ...(rep.gate_reasons || [])].map((s: string) => (
            <p key={s} className="text-sm">
              {s}
            </p>
          ))}
          <p className="text-xs">
            完整评分：基准 {rep.coverage.old.evaluated}/{rep.coverage.old.planned}，本次{' '}
            {rep.coverage.new.evaluated}/{rep.coverage.new.planned}
          </p>
          {rep.comparable && (
            <>
              <div className="text-sm">
                通过率 {(rep.old_pass_rate * 100).toFixed(1)}% →{' '}
                {(rep.new_pass_rate * 100).toFixed(1)}% · 总分 {rep.old_score_avg.toFixed(3)} →{' '}
                {rep.new_score_avg.toFixed(3)}
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className="text-left">评分项</th>
                    <th>基准</th>
                    <th>本次</th>
                    <th>变化</th>
                  </tr>
                </thead>
                <tbody>
                  {rep.by_rubric.map((r: any) => (
                    <tr key={r.rubric_id} className="border-t">
                      <td className="py-2">{r.rubric_id}</td>
                      <td className="text-center">{r.old_avg?.toFixed(3) ?? '—'}</td>
                      <td className="text-center">{r.new_avg?.toFixed(3) ?? '—'}</td>
                      <td
                        className={`text-center ${r.significance === 'regress' ? 'text-destructive' : ''}`}
                      >
                        {r.delta?.toFixed(3) ?? '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </Card>
      )}
      <CandidateRun taskId={taskId} testId={testId} />
    </div>
  )
}

export function CoveragePanel({ testId }: { testId: string }) {
  const query = useQuery({
    queryKey: ['coverage', testId],
    queryFn: () => api.get(`/tests/${testId}/coverage`).then((r) => r.data),
  })
  return (
    <details className="mt-3">
      <summary className="text-sm cursor-pointer">计划覆盖与实际证据</summary>
      <ErrorLine error={query.error} />
      {query.data && (
        <div className="space-y-2 mt-2">
          <p className="text-xs text-muted-foreground">{query.data.note}</p>
          <table className="w-full text-xs">
            <thead>
              <tr>
                <th className="text-left">节点</th>
                <th>计划用例</th>
                <th>记录访问</th>
                <th>关联项完成评分</th>
                <th>对话证据</th>
              </tr>
            </thead>
            <tbody>
              {query.data.nodes.map((n: any) => (
                <tr key={n.node_id} className="border-t">
                  <td className="py-2">{n.label}</td>
                  <td className="text-center">{n.planned_cases}</td>
                  <td className="text-center">
                    {query.data.path_recording === 'unavailable' ? '未知' : n.observed_cases}
                  </td>
                  <td className="text-center">{n.rubric_scored_cases}</td>
                  <td>
                    {n.evidence?.length > 0 && (
                      <details>
                        <summary className="cursor-pointer">{n.evidence.length} 条节点记录</summary>
                        {n.evidence.map((e: any) => (
                          <CaseViewer
                            key={e.case_id + ':' + e.turn}
                            testId={testId}
                            caseId={e.case_id}
                            turn={e.turn}
                          />
                        ))}
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  )
}
