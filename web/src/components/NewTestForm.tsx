import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Plus, Rocket } from 'lucide-react'
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from 'recharts'

import { TasksAPI, TestsAPI, type PersonaInfo, type NewTestRequest } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'

const PIE_COLORS = ['#18181b', '#71717a', '#a1a1aa', '#d4d4d8',
                       '#e4e4e7', '#52525b', '#27272a', '#3f3f46']

const ATTITUDES = ['cooperative', 'refuse', 'hesitant', 'argumentative',
                       'confused', 'blunt', 'hurried']
const MBTI_LIST = (() => {
  const out = []
  for (const a of 'IE') for (const b of 'NS')
    for (const c of 'FT') for (const d of 'JP')
      out.push(a + b + c + d)
  return out
})()
const AGES = ['<20', '20-29', '30-39', '40-49', '50+']
const GENDERS = ['male', 'female']
const EDUS = ['primary', 'middle', 'high', 'college', 'postgrad']


interface Props {
  taskId: string
  onCancel: () => void
  onStarted: (jobId: string, testId: string) => void
}


export function NewTestForm({ taskId, onCancel, onStarted }: Props) {
  // form state
  const [testId, setTestId] = useState(
    `test_${new Date().toISOString().slice(5, 16).replace(/[-:T]/g, '_')}`)
  const [total, setTotal] = useState(30)
  const [noJudge, setNoJudge] = useState(false)
  const [autoRec, setAutoRec] = useState(false)
  const [weights, setWeights] = useState<Record<string, number>>({})

  // dimension filters
  const [fAtti, setFAtti] = useState<string[]>([])
  const [fMbti, setFMbti] = useState<string[]>([])
  const [fGen, setFGen] = useState<string[]>([])
  const [fAge, setFAge] = useState<string[]>([])
  const [fEdu, setFEdu] = useState<string[]>([])

  // personas in this task
  const { data: personaData } = useQuery({
    queryKey: ['task-personas', taskId],
    queryFn: () => TasksAPI.personas(taskId),
  })
  const personas = useMemo(
    () => (personaData?.personas ?? []).filter(p => !p.is_adversarial),
    [personaData])

  // init weights from default once
  useState(() => {
    // empty initially
  })

  const matchFilter = (p: PersonaInfo): boolean => {
    const d = p.demographics
    if (fAtti.length && !fAtti.includes(d.attitude)) return false
    if (fMbti.length && !fMbti.includes(d.mbti)) return false
    if (fGen.length  && !fGen.includes(d.gender)) return false
    if (fAge.length  && !fAge.includes(d.age_range)) return false
    if (fEdu.length  && !fEdu.includes(d.education)) return false
    return true
  }
  const matching = personas.filter(matchFilter)

  const anyFilter = !!(fAtti.length || fMbti.length || fGen.length || fAge.length || fEdu.length)

  const checkMatching = () => {
    const next: Record<string, number> = {}
    matching.forEach(p => {
      next[p.id] = weights[p.id] || p.default_weight || 10
    })
    setWeights(next)
  }
  const clearChecks = () => setWeights({})
  const loadDefault = () => {
    const next: Record<string, number> = {}
    personas.forEach(p => {
      if (p.default_weight > 0) next[p.id] = p.default_weight
    })
    setWeights(next)
  }

  const totalW = Object.values(weights).reduce((s, w) => s + w, 0)
  const pieData = Object.entries(weights)
    .filter(([_, w]) => w > 0)
    .map(([k, v]) => ({ name: k, value: v }))

  // launch
  const qc = useQueryClient()
  const startMut = useMutation({
    mutationFn: (req: NewTestRequest) => TestsAPI.start(taskId, req),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['tests', taskId] })
      onStarted(r.job_id, testId)
    },
  })

  const ready = totalW > 0 && testId.length > 0

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="md" onClick={onCancel}>
          ← 取消
        </Button>
        <h2 className="text-lg font-semibold">新建测试</h2>
      </div>

      {/* basic */}
      <div className="grid grid-cols-4 gap-3">
        <FormField label="测试号 / label">
          <input
            value={testId}
            onChange={e => setTestId(e.target.value)}
            className="input font-mono"
          />
        </FormField>
        <FormField label="--total">
          <input
            type="number"
            value={total}
            onChange={e => setTotal(parseInt(e.target.value) || 30)}
            min={5} max={500} step={5}
            className="input"
          />
        </FormField>
        <FormField label="模式">
          <label className="flex items-center gap-2 text-sm h-9">
            <input
              type="checkbox" checked={noJudge}
              onChange={e => setNoJudge(e.target.checked)}
              className="accent-foreground"
            />
            --no-judge
          </label>
        </FormField>
        <FormField label="跑完">
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

      {/* dimension filters */}
      <div className="border border-border rounded-lg p-4 space-y-3 bg-muted/30">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">🎯 按维度筛选 persona</h3>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={loadDefault}>
              从任务默认权重加载
            </Button>
            <Button
              variant={anyFilter ? 'primary' : 'outline'}
              size="sm"
              onClick={checkMatching}
              disabled={!anyFilter || matching.length === 0}
            >
              ☑ 一键勾符合的({matching.length})
            </Button>
            <Button variant="ghost" size="sm" onClick={clearChecks}>
              ☐ 清空
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-5 gap-2">
          <MultiSelect label="性格" options={ATTITUDES} value={fAtti} onChange={setFAtti} />
          <MultiSelect label="MBTI" options={MBTI_LIST} value={fMbti} onChange={setFMbti} />
          <MultiSelect label="性别" options={GENDERS} value={fGen} onChange={setFGen} />
          <MultiSelect label="年龄" options={AGES} value={fAge} onChange={setFAge} />
          <MultiSelect label="教育" options={EDUS} value={fEdu} onChange={setFEdu} />
        </div>
      </div>

      {/* persona table + pie */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2 border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 border-b border-border">
              <tr className="text-left">
                <th className="px-3 py-2 w-8"></th>
                <th className="px-3 py-2">persona</th>
                <th className="px-3 py-2">MBTI</th>
                <th className="px-3 py-2">性别</th>
                <th className="px-3 py-2">年龄</th>
                <th className="px-3 py-2">教育</th>
                <th className="px-3 py-2">态度</th>
                <th className="px-3 py-2 w-24">权重</th>
              </tr>
            </thead>
            <tbody>
              {personas.length === 0 && (
                <tr><td colSpan={8} className="px-3 py-6 text-center text-muted-foreground">
                  没有 persona,新建任务时会自动生成
                </td></tr>
              )}
              {personas.map(p => {
                const w = weights[p.id] || 0
                const checked = w > 0
                const isMatch = anyFilter && matchFilter(p)
                return (
                  <tr
                    key={p.id}
                    className={`border-b border-border/50 hover:bg-accent/30
                                ${isMatch ? 'bg-warning/5' : ''}`}
                  >
                    <td className="px-3 py-2">
                      <input
                        type="checkbox" checked={checked}
                        onChange={e => {
                          setWeights({
                            ...weights,
                            [p.id]: e.target.checked ? (w || p.default_weight || 10) : 0,
                          })
                        }}
                        className="accent-foreground"
                      />
                    </td>
                    <td className="px-3 py-2 font-mono">{p.id}</td>
                    <td className="px-3 py-2 text-muted-foreground">{p.demographics.mbti}</td>
                    <td className="px-3 py-2 text-muted-foreground">{p.demographics.gender}</td>
                    <td className="px-3 py-2 text-muted-foreground">{p.demographics.age_range}</td>
                    <td className="px-3 py-2 text-muted-foreground">{p.demographics.education}</td>
                    <td className="px-3 py-2 text-muted-foreground">{p.demographics.attitude}</td>
                    <td className="px-3 py-2">
                      <input
                        type="number" value={w}
                        onChange={e => {
                          const v = parseFloat(e.target.value) || 0
                          setWeights({ ...weights, [p.id]: v })
                        }}
                        min={0} step={1}
                        className="w-16 px-2 py-1 border border-border rounded text-sm
                                    focus:outline-none focus:ring-1 focus:ring-foreground/30"
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {/* pie */}
        <div className="border border-border rounded-lg p-3 flex flex-col">
          <div className="text-xs text-muted-foreground mb-1">
            权重比例 · 总 {totalW}
          </div>
          {pieData.length > 0 ? (
            <div className="flex-1 min-h-[200px]">
              <ResponsiveContainer>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%" cy="50%"
                    innerRadius={40} outerRadius={75}
                    paddingAngle={2}
                    dataKey="value"
                  >
                    {pieData.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{
                      fontSize: 12, borderRadius: 6,
                      border: '1px solid hsl(0 0% 90%)',
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground text-xs">
              先勾选 persona
            </div>
          )}
          <div className="text-xs space-y-1 mt-2">
            {pieData.map((d, i) => (
              <div key={d.name} className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-sm"
                       style={{ background: PIE_COLORS[i % PIE_COLORS.length] }} />
                <span className="font-mono">{d.name}</span>
                <span className="ml-auto text-muted-foreground">
                  {Math.round(d.value / totalW * 100)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* launch */}
      <div className="flex items-center justify-between pt-2">
        <div className="text-xs text-muted-foreground">
          {totalW > 0 ?
            `按权重分配 ${total} 个 trial,跑批 ~${Math.ceil(total / 4)} 分钟${autoRec ? ' + 建议 ~3 min' : ''}`
            : '⚠ 至少勾一个 persona 并配权重'}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={onCancel}>取消</Button>
          <Button
            variant="primary"
            disabled={!ready || startMut.isPending}
            onClick={() => startMut.mutate({
              test_id: testId,
              total,
              no_judge: noJudge,
              weights,
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


function FormField({ label, children }: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <label className="text-xs text-muted-foreground">{label}</label>
      {children}
    </div>
  )
}


function MultiSelect({ label, options, value, onChange }: {
  label: string
  options: string[]
  value: string[]
  onChange: (v: string[]) => void
}) {
  return (
    <div className="space-y-1">
      <label className="text-xs text-muted-foreground">{label}</label>
      <details className="border border-border rounded-md group">
        <summary className="px-2 py-1 text-xs cursor-pointer flex items-center
                              justify-between hover:bg-accent">
          <span className="font-medium">
            {value.length === 0 ? '全部' : `${value.length} 选`}
          </span>
          <span className="text-muted-foreground group-open:rotate-180 transition-transform">▾</span>
        </summary>
        <div className="px-2 py-1.5 max-h-44 overflow-auto border-t border-border bg-background">
          {options.map(opt => (
            <label key={opt} className="flex items-center gap-2 py-0.5 text-xs cursor-pointer hover:bg-accent/50 px-1 rounded">
              <input
                type="checkbox"
                checked={value.includes(opt)}
                onChange={e => {
                  if (e.target.checked) onChange([...value, opt])
                  else onChange(value.filter(v => v !== opt))
                }}
                className="accent-foreground"
              />
              <span className="font-mono">{opt}</span>
            </label>
          ))}
        </div>
      </details>
    </div>
  )
}
