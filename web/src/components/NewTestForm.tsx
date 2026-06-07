/**
 * 新建测试表单 v2:5 维度比例 + 独立采样
 *
 * 用户每个维度选要哪些属性、配比例(权重);
 * 系统按维度独立采样生成 N 个 persona 实例。
 */
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Rocket, Eye, RotateCcw } from 'lucide-react'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts'

import { TestsAPI, PersonaLibAPI, type NewTestRequest, type PreviewResult } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'

// 黑灰渐变色系(Linear / Vercel 风格)
const PIE_COLORS = ['#18181b', '#3f3f46', '#52525b', '#71717a',
                       '#a1a1aa', '#d4d4d8', '#27272a', '#09090b']

type DimWeights = Record<string, number>
type AllDims = Record<string, DimWeights>


interface Props {
  taskId: string
  onCancel: () => void
  onStarted: (jobId: string, testId: string) => void
}


export function NewTestForm({ taskId, onCancel, onStarted }: Props) {
  const [testId, setTestId] = useState(
    `test_${new Date().toISOString().slice(5, 16).replace(/[-:T]/g, '_')}`)
  const [total, setTotal] = useState(30)
  const [noJudge, setNoJudge] = useState(false)
  const [autoRec, setAutoRec] = useState(false)

  // 各维度的比例:{ attitude: {cooperative: 60, refuse: 40}, ... }
  const [dims, setDims] = useState<AllDims>({
    attitude: { cooperative: 60, refuse: 30, hesitant: 10 },
    mbti: {},
    gender: {},
    age_range: {},
    education: {},
  })
  // 哪些维度「启用」参与采样(默认只开性格,其余按需勾)
  const [enabledDims, setEnabledDims] = useState<Set<string>>(
    new Set(['attitude']))

  const toggleDimEnabled = (key: string) => {
    setEnabledDims(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  // 实际提交/预览用的维度:只含启用且配了比例的
  const activeDims = useMemo(() => {
    const out: AllDims = {}
    for (const k of enabledDims) {
      if (dims[k] && Object.keys(dims[k]).length > 0) out[k] = dims[k]
    }
    return out
  }, [dims, enabledDims])

  const { data: lib } = useQuery({
    queryKey: ['persona-library'],
    queryFn: PersonaLibAPI.get,
  })

  // 预览
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const previewMut = useMutation({
    mutationFn: () => TestsAPI.previewPersonas(taskId, activeDims, total),
    onSuccess: (r) => setPreview(r),
  })

  // 启动测试
  const qc = useQueryClient()
  const startMut = useMutation({
    mutationFn: (req: NewTestRequest) => TestsAPI.start(taskId, req),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['tests', taskId] })
      onStarted(r.job_id, testId)
    },
  })

  const hasAnyDim = Object.keys(activeDims).length > 0

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="md" onClick={onCancel}>
          ← 取消
        </Button>
        <h2 className="text-lg font-semibold">新建测试</h2>
        <span className="text-sm text-muted-foreground">
          · 按维度比例独立采样生成模拟用户
        </span>
      </div>

      {/* 基本参数 */}
      <div className="grid grid-cols-4 gap-3">
        <FormField label="测试号">
          <input
            value={testId}
            onChange={e => setTestId(e.target.value)}
            className="w-full px-2.5 py-1.5 border border-border rounded-md
                        text-sm font-mono focus:outline-none focus:ring-2 focus:ring-foreground/20"
          />
        </FormField>
        <FormField label="模拟用户数 --total">
          <input
            type="number"
            value={total}
            onChange={e => setTotal(parseInt(e.target.value) || 30)}
            min={5} max={500} step={5}
            className="w-full px-2.5 py-1.5 border border-border rounded-md
                        text-sm focus:outline-none focus:ring-2 focus:ring-foreground/20"
          />
        </FormField>
        <FormField label=" ">
          <label className="flex items-center gap-2 text-sm h-9">
            <input
              type="checkbox" checked={noJudge}
              onChange={e => setNoJudge(e.target.checked)}
              className="accent-foreground"
            />
            no-judge(只跑对话)
          </label>
        </FormField>
        <FormField label=" ">
          <label className="flex items-center gap-2 text-sm h-9">
            <input
              type="checkbox" checked={autoRec}
              onChange={e => setAutoRec(e.target.checked)}
              className="accent-foreground"
            />
            自动出建议 (+3-5min)
          </label>
        </FormField>
      </div>

      {/* 5 维度区 */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">维度配置</h3>
          <div className="text-xs text-muted-foreground">
            勾选要测的维度 · 各维度独立采样交叉组合 · 未启用的维度不参与
          </div>
        </div>

        {lib?.dimensions.map(dim => (
          <DimensionBlock
            key={dim.dim}
            dimKey={dim.dim}
            label={dim.label}
            options={dim.values.map(v => ({ value: v.value, label: v.label, desc: v.desc }))}
            current={dims[dim.dim] || {}}
            enabled={enabledDims.has(dim.dim)}
            onToggleEnabled={() => toggleDimEnabled(dim.dim)}
            onChange={next => setDims({ ...dims, [dim.dim]: next })}
          />
        ))}
      </div>

      {/* 预览按钮 + 结果 */}
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          onClick={() => previewMut.mutate()}
          disabled={!hasAnyDim || previewMut.isPending}
        >
          {previewMut.isPending ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
          预览生成 {total} 个 persona 的实际分布
        </Button>
        {preview && (
          <Button variant="ghost" size="sm" onClick={() => setPreview(null)}>
            <RotateCcw size={12} /> 清除预览
          </Button>
        )}
      </div>

      {preview && (
        <PreviewSection preview={preview} total={total} />
      )}

      {/* 启动按钮 */}
      <div className="flex items-center justify-between border-t border-border pt-4">
        <div className="text-xs text-muted-foreground">
          {hasAnyDim ?
            `按维度采样 ${total} 个 persona,预计 ~${Math.ceil(total / 4)} 分钟${autoRec ? ' + 建议 ~3min' : ''}` :
            '⚠ 至少给一个维度配比例'}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={onCancel}>取消</Button>
          <Button
            variant="primary"
            disabled={!hasAnyDim || startMut.isPending}
            onClick={() => startMut.mutate({
              test_id: testId,
              total, no_judge: noJudge,
              dimensions: activeDims,
              auto_recommend: autoRec,
            })}
          >
            {startMut.isPending ? (
              <><Loader2 size={14} className="animate-spin" />提交中</>
            ) : (
              <><Rocket size={14} />启动测试</>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}


function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-xs text-muted-foreground">{label}</label>
      {children}
    </div>
  )
}


interface DimBlockProps {
  dimKey: string
  label: string
  options: { value: string; label: string; desc: string }[]
  current: DimWeights
  enabled: boolean
  onToggleEnabled: () => void
  onChange: (next: DimWeights) => void
}

function DimensionBlock({ label, options, current, enabled, onToggleEnabled, onChange }: DimBlockProps) {
  const total = Object.values(current).reduce((s, v) => s + v, 0)
  const pieData = Object.entries(current)
    .filter(([_, v]) => v > 0)
    .map(([k, v]) => ({ name: k, value: v }))

  const toggleValue = (val: string) => {
    if (current[val] > 0) {
      const next = { ...current }
      delete next[val]
      onChange(next)
    } else {
      onChange({ ...current, [val]: 10 })
    }
  }

  const setWeight = (val: string, w: number) => {
    if (w <= 0) {
      const next = { ...current }
      delete next[val]
      onChange(next)
    } else {
      onChange({ ...current, [val]: w })
    }
  }

  return (
    <div className={`border rounded-lg p-4 transition-colors
                      ${enabled ? 'border-border' : 'border-border/50 bg-muted/20'}`}>
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={onToggleEnabled}
          className="flex items-center gap-2 group"
        >
          {/* toggle switch */}
          <span className={`relative inline-flex h-4 w-7 shrink-0 items-center rounded-full
                            transition-colors ${enabled ? 'bg-foreground' : 'bg-border'}`}>
            <span className={`inline-block h-3 w-3 transform rounded-full bg-background
                              transition-transform ${enabled ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
          </span>
          <h4 className={`text-sm font-semibold ${enabled ? '' : 'text-muted-foreground'}`}>
            {label}
          </h4>
          {enabled ? (
            <span className="text-xs text-muted-foreground">
              {Object.keys(current).length} / {options.length} 选 · 总 {total}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">未启用(不参与采样)</span>
          )}
        </button>
        {enabled && Object.keys(current).length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => onChange({})}>
            清空
          </Button>
        )}
      </div>

      {!enabled ? null : (
      <div className="grid grid-cols-[1fr_180px] gap-4">
        {/* 属性勾选 + 比例 */}
        <div className="grid grid-cols-2 gap-1.5">
          {options.map(opt => {
            const w = current[opt.value] || 0
            const checked = w > 0
            return (
              <div
                key={opt.value}
                className={`flex items-center gap-2 px-2 py-1 rounded border
                            ${checked ? 'border-foreground bg-foreground/5' : 'border-border'}
                            transition-colors`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleValue(opt.value)}
                  className="accent-foreground"
                />
                <span className="font-mono text-xs flex-1 truncate" title={opt.label}>
                  {opt.value}
                </span>
                {opt.desc && opt.desc !== opt.label && (
                  <span className="text-[10px] text-muted-foreground truncate">{opt.desc}</span>
                )}
                {checked && (
                  <input
                    type="number"
                    value={w}
                    onChange={e => setWeight(opt.value, parseFloat(e.target.value) || 0)}
                    min={0} step={1}
                    className="w-12 px-1 py-0.5 text-xs border border-border rounded
                                font-mono text-right
                                focus:outline-none focus:ring-1 focus:ring-foreground/30"
                  />
                )}
              </div>
            )
          })}
        </div>

        {/* mini 饼图 */}
        <div className="flex flex-col">
          {pieData.length > 0 ? (
            <>
              <div className="flex-1 h-[120px]">
                <ResponsiveContainer>
                  <PieChart>
                    <Pie
                      data={pieData}
                      cx="50%" cy="50%"
                      innerRadius={28} outerRadius={50}
                      paddingAngle={2}
                      dataKey="value"
                    >
                      {pieData.map((_, i) => (
                        <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={{ fontSize: 11, borderRadius: 4 }} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="text-[10px] space-y-0.5 mt-1">
                {pieData.map((d, i) => (
                  <div key={d.name} className="flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-sm shrink-0"
                           style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                    <span className="font-mono truncate">{d.name}</span>
                    <span className="ml-auto text-muted-foreground shrink-0">
                      {Math.round(d.value / total * 100)}%
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center
                            text-[10px] text-muted-foreground border border-dashed
                            border-border rounded">
              未配置
            </div>
          )}
        </div>
      </div>
      )}
    </div>
  )
}


function PreviewSection({ preview, total }: { preview: PreviewResult; total: number }) {
  return (
    <div className="border border-border rounded-lg p-4 bg-muted/20 space-y-3">
      <div className="text-sm font-semibold">
        📊 预览:实际采样 {total} 个 persona 的分布
      </div>
      <div className="grid grid-cols-5 gap-3">
        {Object.entries(preview.distribution).map(([dim, counts]) => (
          <div key={dim}>
            <div className="text-xs font-semibold text-muted-foreground mb-1">
              {dim}
            </div>
            <div className="text-[11px] space-y-0.5">
              {Object.entries(counts)
                .sort(([, a], [, b]) => b - a)
                .map(([k, n]) => (
                  <div key={k} className="flex justify-between">
                    <span className="font-mono truncate" title={k}>{k}</span>
                    <span className="text-muted-foreground">
                      {n} ({Math.round(n / total * 100)}%)
                    </span>
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>
      <div>
        <div className="text-xs font-semibold mb-1">前 10 个样本</div>
        <div className="text-[11px] font-mono space-y-0.5">
          {preview.samples.map((s, i) => (
            <div key={i} className="text-muted-foreground">
              <span className="inline-block w-6">{i + 1}</span>
              {' '}{s.attitude} · {s.mbti} · {s.gender} · {s.age_range} · {s.education}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
