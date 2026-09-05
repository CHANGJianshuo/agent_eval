import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, TestsAPI } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { ErrorLine } from '@/components/TaskConfig'

const input = 'border border-border rounded px-2 py-1.5 text-sm bg-background'
export default function MetaEval() {
  const { taskId = '' } = useParams()
  const qc = useQueryClient()
  const [actor, setActor] = useState(() => localStorage.getItem('eval-annotator') || '')
  const [batch, setBatch] = useState('')
  const [run, setRun] = useState('')
  const [mode, setMode] = useState('independent')
  const [n, setN] = useState(30)
  const [active, setActive] = useState('')
  const batches = useQuery({
    queryKey: ['meta-batches', taskId],
    queryFn: () => api.get(`/tasks/${taskId}/meta-eval/batches`).then((r) => r.data),
  })
  const runs = useQuery({ queryKey: ['tests', taskId], queryFn: () => TestsAPI.listByTask(taskId) })
  const batchId = batch || batches.data?.batches?.[0]?.batch_id || ''
  const samples = useQuery({
    queryKey: ['meta-samples', taskId, batchId, actor],
    queryFn: () =>
      api
        .get(`/tasks/${taskId}/meta-eval/samples`, {
          params: { batch_id: batchId || undefined, annotator: actor.trim() },
        })
        .then((r) => r.data),
  })
  const ready =
    (samples.data?.n_total || 0) > 0 && samples.data?.n_annotated === samples.data?.n_total
  const report = useQuery({
    queryKey: ['meta-report', taskId, batchId, actor],
    queryFn: () =>
      api
        .get(`/tasks/${taskId}/meta-eval/report`, {
          params: { batch_id: batchId || undefined, annotator: actor.trim() },
        })
        .then((r) => r.data),
    enabled: ready || samples.data?.mode === 'assisted',
  })
  const create = useMutation({
    mutationFn: () =>
      api
        .post(`/tasks/${taskId}/meta-eval/sample`, { n, run_id: run || undefined, mode })
        .then((r) => r.data),
    onSuccess: (d) => {
      setBatch(d.batch_id)
      setActive('')
      qc.invalidateQueries({ queryKey: ['meta-batches', taskId] })
      qc.invalidateQueries({ queryKey: ['meta-samples', taskId] })
    },
  })
  const rep = report.data
  return (
    <div className="space-y-5">
      <div className="flex gap-3 items-center">
        <Link to={`/tasks/${taskId}`}>
          <Button variant="ghost">← 返回任务</Button>
        </Link>
        <h1 className="text-xl font-semibold">人工校准</h1>
      </div>
      <Card className="p-4 space-y-3">
        <p className="text-sm">
          独立评分先阅读实际检查标准与对话，再填写人工分。提交后才展示 Judge
          答案；不同标注者的结果分别保存。
        </p>
        <div className="flex gap-3 flex-wrap items-end">
          <label className="text-xs">
            标注者标识
            <input
              aria-label="标注者标识"
              placeholder="填写姓名或固定代号"
              className={`${input} block`}
              value={actor}
              onChange={(e) => {
                setActor(e.target.value)
                localStorage.setItem('eval-annotator', e.target.value)
              }}
            />
          </label>
          <label className="text-xs">
            抽样来源
            <select
              aria-label="抽样来源"
              className={`${input} block max-w-64`}
              value={run}
              onChange={(e) => setRun(e.target.value)}
            >
              <option value="">请选择运行</option>
              {runs.data?.map((r) => (
                <option key={r.test_id} value={r.test_id}>
                  {r.test_id}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs">
            方式
            <select
              className={`${input} block`}
              value={mode}
              onChange={(e) => setMode(e.target.value)}
            >
              <option value="independent">独立评分</option>
              <option value="assisted">辅助复核（先看 Judge）</option>
            </select>
          </label>
          <label className="text-xs">
            抽样数
            <input
              aria-label="抽样数"
              className={`${input} block w-20`}
              type="number"
              min={1}
              max={1000}
              value={n}
              onChange={(e) => setN(Number(e.target.value))}
            />
          </label>
          <Button
            variant="primary"
            disabled={
              create.isPending || !actor.trim() || !run || !Number.isInteger(n) || n < 1 || n > 1000
            }
            onClick={() => create.mutate()}
          >
            创建新批次
          </Button>
        </div>
        <ErrorLine error={create.error || runs.error} />
      </Card>
      <div className="flex gap-3 items-center">
        <label className="text-sm flex-1">
          标注批次
          <select
            aria-label="标注批次"
            className={`${input} w-full block`}
            value={batchId}
            onChange={(e) => {
              setBatch(e.target.value)
              setActive('')
            }}
          >
            <option value="">旧版 / 暂无批次</option>
            {batches.data?.batches?.map((b: any) => (
              <option key={b.batch_id} value={b.batch_id}>
                {b.created_at} · {b.n_samples} 条 · {b.mode === 'independent' ? '独立' : '辅助'} ·{' '}
                {b.run_id}
              </option>
            ))}
          </select>
        </label>
        <p className="text-sm">
          本人进度 {samples.data?.n_annotated || 0}/{samples.data?.n_total || 0}
        </p>
      </div>
      <ErrorLine error={samples.error || batches.error || report.error} />
      {samples.data?.rubrics_in_pool != null && (
        <p className="text-xs text-muted-foreground">
          实际抽到 {samples.data.rubrics_sampled}/{samples.data.rubrics_in_pool}{' '}
          个评分项。新建批次保留旧样本与标注，不保证少量样本覆盖全部检查项。
        </p>
      )}
      {!actor.trim() && (
        <p className="text-sm text-warning">请先填写固定标注者标识，再开始评分。</p>
      )}
      {samples.data?.samples.map((item: any) => (
        <Card key={item.item_id} className="overflow-hidden">
          <button
            className="w-full text-left p-4 flex justify-between text-sm"
            onClick={() => setActive(active === item.item_id ? '' : item.item_id)}
          >
            <span>
              {item.rubric_id} · {item.script_id}
            </span>
            <span>
              {item.annotated
                ? '已标注'
                : item.judge_score == null
                  ? '待独立评分'
                  : `Judge ${item.judge_score.toFixed(2)}`}
            </span>
          </button>
          {active === item.item_id && (
            <ReviewItem
              key={item.item_id + actor + batchId}
              taskId={taskId}
              item={item}
              actor={actor.trim()}
              batchId={batchId}
              mode={samples.data.mode}
              onSaved={() => {
                qc.invalidateQueries({ queryKey: ['meta-samples', taskId] })
                qc.invalidateQueries({ queryKey: ['meta-report', taskId] })
                qc.invalidateQueries({ queryKey: ['meta-conversation', taskId] })
              }}
            />
          )}
        </Card>
      ))}
      {!ready && samples.data?.mode === 'independent' && (
        <p className="text-xs text-muted-foreground">
          完成本批次的个人评分后展示校准统计，避免提前看到 Judge 结论影响判断。
        </p>
      )}
      {rep && (
        <Card className="p-4 space-y-3">
          <h2 className="font-medium">本批次校准统计</h2>
          <p className="text-sm">
            覆盖 {rep.n_annotated}/{rep.n_samples} 个样本 · {rep.n_ratings} 次人工评分，其中独立评分{' '}
            {rep.independent_ratings} 次
          </p>
          <p className="text-sm">
            人机一致率 {(rep.agreement_rate * 100).toFixed(1)}%（分差 ≤ 0.2） · 平均偏差{' '}
            {rep.mean_bias.toFixed(3)}
          </p>
          <p className="text-xs text-warning">
            {rep.sufficient_sample
              ? '达到 20 个独立样本的最低参考门槛，仍不能直接判定评委可信。'
              : '独立样本不足 20 个，仅用于初步复核。'}
            {rep.scope}
          </p>
          <table className="w-full text-xs">
            <thead>
              <tr>
                <th className="text-left">评分项</th>
                <th>样本 / 评分次数</th>
                <th>一致率</th>
                <th>偏差</th>
                <th>样本说明</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(rep.by_rubric).map(([rid, row]: [string, any]) => (
                <tr key={rid} className="border-t">
                  <td className="py-2">{rid}</td>
                  <td className="text-center">
                    {row.n_items}/{row.n}
                  </td>
                  <td className="text-center">{(row.agreement_rate * 100).toFixed(1)}%</td>
                  <td className="text-center">{row.mean_bias.toFixed(3)}</td>
                  <td className="text-center">
                    {row.sufficient_sample ? '至少 5 个独立样本' : '样本不足'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rep.annotator_disagreements.length > 0 && (
            <details>
              <summary className="text-sm cursor-pointer">
                标注者之间的分歧（{rep.annotator_disagreements.length} 个样本）
              </summary>
              {rep.annotator_disagreements.map((d: any) => (
                <p key={d.item_id} className="text-xs py-2">
                  {d.item_id}：{d.ratings.map((r: any) => `${r.annotator}: ${r.score}`).join('；')}
                </p>
              ))}
            </details>
          )}
        </Card>
      )}
    </div>
  )
}

function ReviewItem({
  taskId,
  item,
  actor,
  batchId,
  mode,
  onSaved,
}: {
  taskId: string
  item: any
  actor: string
  batchId: string
  mode: string
  onSaved: () => void
}) {
  const [score, setScore] = useState(
    item.annotation?.human_score == null ? '' : String(item.annotation.human_score),
  )
  const [comment, setComment] = useState(item.annotation?.comment || '')
  const query = useQuery({
    queryKey: ['meta-conversation', taskId, item.item_id, batchId, actor],
    queryFn: () =>
      api
        .get(`/tasks/${taskId}/meta-eval/items/${encodeURIComponent(item.item_id)}/conversation`, {
          params: { batch_id: batchId || undefined, annotator: actor },
        })
        .then((r) => r.data),
  })
  const submit = useMutation({
    mutationFn: (agree: boolean) =>
      api.post(`/tasks/${taskId}/meta-eval/annotations`, {
        item_id: item.item_id,
        batch_id: batchId || undefined,
        annotator: actor,
        agree,
        human_score: agree ? null : Number(score),
        comment,
      }),
    onSuccess: onSaved,
  })
  const valid =
    score.trim() !== '' &&
    Number.isFinite(Number(score)) &&
    Number(score) >= 0 &&
    Number(score) <= 1
  return (
    <div className="p-4 border-t space-y-3">
      <p className="text-xs font-medium">实际检查标准</p>
      <p className="text-sm whitespace-pre-wrap">
        {item.rubric_check || '旧运行未保存检查文本，仅支持辅助复核'}
      </p>
      {item.judge_score != null && (
        <p className="text-xs bg-muted p-3">
          Judge：{item.judge_score.toFixed(2)} · {item.judge_reasoning}
        </p>
      )}
      <div className="max-h-80 overflow-auto space-y-2">
        {query.data?.turns.map((t: any) => (
          <div
            key={t.turn}
            className={`text-xs p-2 rounded ${t.role === 'assistant' ? 'bg-blue-50' : 'bg-muted'}`}
          >
            <strong>
              #{t.turn} {t.role === 'assistant' ? '被测模型' : '模拟用户'}
            </strong>
            <p className="whitespace-pre-wrap">{t.text}</p>
          </div>
        ))}
      </div>
      <ErrorLine error={query.error || submit.error} />
      <div className="flex gap-2 items-center flex-wrap">
        <label className="text-xs">
          人工分
          <input
            aria-label="人工分"
            className={`${input} block w-24`}
            type="number"
            min={0}
            max={1}
            step={0.1}
            value={score}
            onChange={(e) => setScore(e.target.value)}
          />
        </label>
        <input
          aria-label="标注原因"
          className={`${input} flex-1`}
          placeholder="评分依据或不同意见"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <Button
          variant="primary"
          disabled={!actor || !valid || submit.isPending || !query.data || query.isError}
          onClick={() => submit.mutate(false)}
        >
          提交人工分
        </Button>
        {mode === 'assisted' && (
          <Button
            variant="outline"
            disabled={!actor || submit.isPending || item.judge_score == null || !query.data}
            onClick={() => submit.mutate(true)}
          >
            同意 Judge 评分
          </Button>
        )}
      </div>
    </div>
  )
}
