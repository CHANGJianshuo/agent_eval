/**
 * Meta-Eval 人工校准页
 *
 * 回答「评测系统本身可信吗」:
 * 1. 抽样 N 条 LLM Judge 评分 → 2. 人工逐条标注(同意/不同意+人工分)
 * → 3. 校准报告(人机一致率 / 按 rubric / 系统性偏差)
 */
import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Loader2, CheckCircle2, XCircle, RefreshCw, Dices } from 'lucide-react'

import { api } from '@/lib/api'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'

interface SampleItem {
  item_id: string
  rubric_id: string
  dimension: string
  judge_score: number
  judge_reasoning: string
  evidence_turn: number | null
  script_id: string
  run_id: string
  annotated: boolean
  annotation: { agree: boolean; human_score: number | null; comment: string } | null
}

interface CalibReport {
  n_samples: number
  n_annotated: number
  agreement_rate: number
  mean_bias: number
  by_rubric: Record<string, { n: number; agreement_rate: number; mean_bias: number }>
  disagreements: Array<{
    item_id: string; rubric_id: string
    judge_score: number; human_score: number; comment: string
  }>
}


export default function MetaEval() {
  const { taskId = '' } = useParams<{ taskId: string }>()
  const qc = useQueryClient()
  const [activeItem, setActiveItem] = useState<string | null>(null)

  const { data: samplesData } = useQuery({
    queryKey: ['meta-eval-samples', taskId],
    queryFn: () => api.get<{ samples: SampleItem[]; n_total: number; n_annotated: number }>(
      `/tasks/${taskId}/meta-eval/samples`).then(r => r.data),
  })

  const { data: report } = useQuery({
    queryKey: ['meta-eval-report', taskId],
    queryFn: () => api.get<CalibReport>(`/tasks/${taskId}/meta-eval/report`).then(r => r.data),
    enabled: (samplesData?.n_annotated ?? 0) > 0,
  })

  const [sampleN, setSampleN] = useState(30)
  const sampleMut = useMutation({
    mutationFn: () => api.post(`/tasks/${taskId}/meta-eval/sample`, { n: sampleN }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['meta-eval-samples', taskId] })
    },
  })

  const samples = samplesData?.samples ?? []
  const nAnnotated = samplesData?.n_annotated ?? 0
  const nTotal = samplesData?.n_total ?? 0

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <Link to={`/tasks/${taskId}`}>
          <Button variant="ghost" size="md">
            <ArrowLeft size={14} /> {taskId}
          </Button>
        </Link>
        <h1 className="text-xl font-semibold">Meta-Eval 人工校准</h1>
        <span className="text-xs text-muted-foreground">
          验证「自动评分是否可信」:人工复核 LLM Judge 的评分,算人机一致率
        </span>
      </div>

      {/* 抽样控制 + 进度 */}
      <Card className="p-4 flex items-center gap-4">
        <div className="flex items-center gap-2">
          <input
            type="number" value={sampleN}
            onChange={e => setSampleN(parseInt(e.target.value) || 30)}
            min={5} max={200}
            className="w-20 px-2 py-1.5 border border-border rounded-md text-sm"
          />
          <Button
            variant="outline"
            onClick={() => sampleMut.mutate()}
            disabled={sampleMut.isPending}
          >
            {sampleMut.isPending
              ? <Loader2 size={14} className="animate-spin" />
              : <Dices size={14} />}
            {nTotal > 0 ? '重新抽样' : '抽样'}
          </Button>
          {sampleMut.isError && (
            <span className="text-xs text-destructive">
              {(sampleMut.error as any)?.response?.data?.detail || '抽样失败'}
            </span>
          )}
        </div>
        <div className="text-sm text-muted-foreground">
          {nTotal > 0
            ? <>标注进度 <strong className="text-foreground">{nAnnotated}/{nTotal}</strong>
                · 分层抽样保证全部 rubric 覆盖 + 高低分混合</>
            : '还没抽样。抽样会从已有评分结果里分层选出待人工复核的 Judge 评分。'}
        </div>
      </Card>

      {/* 校准报告 */}
      {report && report.n_annotated > 0 && (
        <Card className="p-5 space-y-3">
          <h2 className="text-sm font-semibold">校准报告</h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="text-center p-3 bg-muted/40 rounded">
              <div className={`text-2xl font-semibold
                  ${report.agreement_rate >= 0.85 ? 'text-success'
                    : report.agreement_rate >= 0.7 ? 'text-warning' : 'text-destructive'}`}>
                {(report.agreement_rate * 100).toFixed(0)}%
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                人机一致率(|judge−human| ≤ 0.2)
              </div>
            </div>
            <div className="text-center p-3 bg-muted/40 rounded">
              <div className="text-2xl font-semibold">
                {report.mean_bias > 0 ? '+' : ''}{report.mean_bias.toFixed(3)}
              </div>
              <div className="text-xs text-muted-foreground mt-1">
                系统性偏差({report.mean_bias > 0.05 ? 'Judge 偏松'
                  : report.mean_bias < -0.05 ? 'Judge 偏严' : '无明显偏差'})
              </div>
            </div>
            <div className="text-center p-3 bg-muted/40 rounded">
              <div className="text-2xl font-semibold">{report.disagreements.length}</div>
              <div className="text-xs text-muted-foreground mt-1">分歧 case</div>
            </div>
          </div>

          {Object.keys(report.by_rubric).length > 0 && (
            <table className="w-full text-xs mt-2">
              <thead>
                <tr className="border-b border-border text-muted-foreground">
                  <th className="text-left py-1.5">Rubric</th>
                  <th className="text-center">标注数</th>
                  <th className="text-center">一致率</th>
                  <th className="text-center">偏差</th>
                  <th className="text-left">评估</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(report.by_rubric)
                  .sort(([, a], [, b]) => a.agreement_rate - b.agreement_rate)
                  .map(([rid, br]) => (
                  <tr key={rid} className="border-b border-border/40">
                    <td className="py-1.5 font-mono">{rid}</td>
                    <td className="text-center">{br.n}</td>
                    <td className={`text-center font-medium
                        ${br.agreement_rate >= 0.85 ? 'text-success'
                          : br.agreement_rate >= 0.7 ? 'text-warning' : 'text-destructive'}`}>
                      {(br.agreement_rate * 100).toFixed(0)}%
                    </td>
                    <td className="text-center font-mono">
                      {br.mean_bias > 0 ? '+' : ''}{br.mean_bias.toFixed(2)}
                    </td>
                    <td className="text-muted-foreground">
                      {br.agreement_rate < 0.7
                        ? '⚠ 不可靠 — 建议改 check 描述或换规则 matcher'
                        : br.agreement_rate < 0.85 ? '基本可信' : '可信'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      )}

      {/* 标注任务列表 */}
      {samples.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold">
            标注任务({samples.length})
            <span className="font-normal text-xs text-muted-foreground ml-2">
              点开一条 → 看对话和 Judge 评分 → 同意 / 给出人工分
            </span>
          </h2>
          {samples.map(s => (
            <AnnotationRow
              key={s.item_id}
              taskId={taskId}
              item={s}
              expanded={activeItem === s.item_id}
              onToggle={() => setActiveItem(
                activeItem === s.item_id ? null : s.item_id)}
              onSaved={() => {
                qc.invalidateQueries({ queryKey: ['meta-eval-samples', taskId] })
                qc.invalidateQueries({ queryKey: ['meta-eval-report', taskId] })
                setActiveItem(null)
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}


function AnnotationRow({ taskId, item, expanded, onToggle, onSaved }: {
  taskId: string
  item: SampleItem
  expanded: boolean
  onToggle: () => void
  onSaved: () => void
}) {
  return (
    <Card className={expanded ? 'border-foreground/30' : ''}>
      <button
        onClick={onToggle}
        className="w-full px-4 py-2.5 flex items-center gap-3 text-left hover:bg-accent/40 transition-colors"
      >
        {item.annotated
          ? (item.annotation?.agree
            ? <CheckCircle2 size={15} className="text-success shrink-0" />
            : <XCircle size={15} className="text-warning shrink-0" />)
          : <div className="w-[15px] h-[15px] rounded-full border border-border shrink-0" />}
        <code className="text-xs font-semibold">{item.rubric_id}</code>
        <Badge variant="default">{item.dimension}</Badge>
        <span className="text-xs text-muted-foreground">剧本 {item.script_id}</span>
        <span className="ml-auto text-sm font-mono font-medium">
          judge {item.judge_score.toFixed(2)}
        </span>
        {item.annotated && !item.annotation?.agree && (
          <span className="text-sm font-mono text-warning">
            human {item.annotation?.human_score?.toFixed(2)}
          </span>
        )}
      </button>
      {expanded && (
        <AnnotationDetail taskId={taskId} item={item} onSaved={onSaved} />
      )}
    </Card>
  )
}


function AnnotationDetail({ taskId, item, onSaved }: {
  taskId: string
  item: SampleItem
  onSaved: () => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['meta-eval-conv', item.item_id],
    queryFn: () => api.get<{ turns: Array<{ turn: number; role: string; text: string; is_probe: boolean }> }>(
      `/tasks/${taskId}/meta-eval/items/${encodeURIComponent(item.item_id)}/conversation`
    ).then(r => r.data),
  })

  const [humanScore, setHumanScore] = useState(item.judge_score)
  const [comment, setComment] = useState('')

  const submitMut = useMutation({
    mutationFn: (req: { agree: boolean; human_score?: number; comment?: string }) =>
      api.post(`/tasks/${taskId}/meta-eval/annotations`, {
        item_id: item.item_id, ...req,
      }),
    onSuccess: onSaved,
  })

  return (
    <div className="border-t border-border px-4 py-3 space-y-3">
      {/* Judge 评分信息 */}
      <div className="bg-muted/40 rounded p-3 text-xs space-y-1">
        <div>
          <span className="text-muted-foreground">Judge 评分:</span>{' '}
          <strong className="font-mono">{item.judge_score.toFixed(2)}</strong>
          {item.evidence_turn != null && (
            <span className="text-muted-foreground ml-3">
              证据轮次:第 {item.evidence_turn} 轮
            </span>
          )}
        </div>
        <div>
          <span className="text-muted-foreground">理由:</span>{' '}
          {item.judge_reasoning || '(无)'}
        </div>
      </div>

      {/* 对话回放 */}
      <div className="max-h-72 overflow-y-auto space-y-1.5 border border-border rounded p-3">
        {isLoading && <Loader2 size={14} className="animate-spin" />}
        {data?.turns.map(t => (
          <div
            key={t.turn}
            className={`text-xs px-2.5 py-1.5 rounded max-w-[85%]
                ${t.role === 'assistant'
                  ? 'bg-blue-50 border-l-2 border-blue-400'
                  : 'bg-muted ml-auto'}
                ${item.evidence_turn === t.turn ? 'ring-2 ring-amber-400' : ''}`}
          >
            <span className="text-muted-foreground mr-1.5">
              #{t.turn} {t.role === 'assistant' ? '客服(SUT)' : '用户(模拟)'}
              {t.is_probe && ' 🎯探针'}
            </span>
            {t.text}
          </div>
        ))}
        {data && data.turns.length === 0 && (
          <div className="text-xs text-muted-foreground">trace 内容为空</div>
        )}
      </div>

      {/* 标注操作 */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button
          variant="primary"
          size="sm"
          disabled={submitMut.isPending}
          onClick={() => submitMut.mutate({ agree: true })}
        >
          <CheckCircle2 size={13} /> 同意 Judge 评分
        </Button>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">不同意,人工分:</span>
          <input
            type="number" min={0} max={1} step={0.1}
            value={humanScore}
            onChange={e => setHumanScore(parseFloat(e.target.value))}
            className="w-16 px-2 py-1 border border-border rounded text-sm font-mono"
          />
          <input
            placeholder="原因(可选)"
            value={comment}
            onChange={e => setComment(e.target.value)}
            className="w-56 px-2 py-1 border border-border rounded text-sm"
          />
          <Button
            variant="outline"
            size="sm"
            disabled={submitMut.isPending || isNaN(humanScore)}
            onClick={() => submitMut.mutate({
              agree: false, human_score: humanScore, comment,
            })}
          >
            <XCircle size={13} /> 提交人工分
          </Button>
        </div>
        {submitMut.isPending && <Loader2 size={14} className="animate-spin" />}
        {submitMut.isError && (
          <span className="text-xs text-destructive">提交失败</span>
        )}
      </div>
    </div>
  )
}
