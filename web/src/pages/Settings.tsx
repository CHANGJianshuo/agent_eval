import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Eye, EyeOff, Plus, Trash2, ChevronDown, Pencil, X, Check } from 'lucide-react'

import { ConfigAPI, PersonaLibAPI, type PersonaDimension, type PersonaDimensionValue } from '@/lib/api'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'


export default function Settings() {
  const [tab, setTab] = useState<'api' | 'persona' | 'noise'>('api')
  return (
    <div className="space-y-5 max-w-5xl">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">全局配置</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          跨任务复用的资源(API & 模型 / Persona 库 / 噪音库)
        </p>
      </div>
      <div className="flex gap-1 border-b border-border">
        {([
          ['api', '🔑 API & 模型'],
          ['persona', '🎭 Persona 库'],
          ['noise', '📚 噪音库'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
                        ${tab === key
                          ? 'border-foreground text-foreground'
                          : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="pt-2">
        {tab === 'api' && <ApiSection />}
        {tab === 'persona' && <PersonaSection />}
        {tab === 'noise' && <NoiseSection />}
      </div>
    </div>
  )
}


// ============================ API & 模型 ============================

function ApiSection() {
  const qc = useQueryClient()
  const { data: keys } = useQuery({
    queryKey: ['api-keys'],
    queryFn: ConfigAPI.listKeys,
  })
  const { data: models } = useQuery({
    queryKey: ['models'],
    queryFn: ConfigAPI.getModels,
  })

  const [keyDraft, setKeyDraft] = useState<Record<string, string>>({})
  const [showKey, setShowKey] = useState<Record<string, boolean>>({})
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; msg: string } | null>>({})

  const saveKey = useMutation({
    mutationFn: ({ provider, api_key }: { provider: string; api_key: string }) =>
      ConfigAPI.saveKey(provider, api_key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['api-keys'] })
      setKeyDraft({})
    },
  })
  const testConn = useMutation({
    mutationFn: ({ provider, api_key }: { provider: string; api_key?: string }) =>
      ConfigAPI.testConnection(provider, api_key),
    onSuccess: (r, vars) => setTestResult(prev => ({
      ...prev, [vars.provider]: { ok: r.ok, msg: r.message },
    })),
  })

  const saveModels = useMutation({
    mutationFn: (cfg: any) => ConfigAPI.updateModels(cfg),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['models'] }),
  })
  const [modelDraft, setModelDraft] = useState<any | null>(null)
  const cur = modelDraft ?? models

  const DEFAULT_PROVIDERS = [
    { id: 'deepseek', name: 'DeepSeek' },
    { id: 'xiaomi_mimo', name: '小米 MiMo' },
    { id: 'openai', name: 'OpenAI' },
    { id: 'anthropic', name: 'Anthropic' },
  ]

  const [customProviders, setCustomProviders] = useState<Array<{ id: string; name: string }>>([])
  const [addingProvider, setAddingProvider] = useState(false)
  const [newProviderId, setNewProviderId] = useState('')
  const [newProviderName, setNewProviderName] = useState('')

  const allProviders = [...DEFAULT_PROVIDERS, ...customProviders]
  // 后端返回的 key 列表里可能有我们不认识的 provider,也加上
  const extraFromBackend = keys
    ? Object.keys(keys).filter(k => !allProviders.some(p => p.id === k))
    : []
  const providers = [
    ...allProviders,
    ...extraFromBackend.map(id => ({ id, name: id })),
  ]

  const handleAddProvider = () => {
    const id = newProviderId.trim().toLowerCase().replace(/\s+/g, '_')
    const name = newProviderName.trim()
    if (!id || !name) return
    if (providers.some(p => p.id === id)) return
    setCustomProviders([...customProviders, { id, name }])
    setNewProviderId('')
    setNewProviderName('')
    setAddingProvider(false)
  }

  return (
    <div className="space-y-5">
      <Card className="p-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">🔑 API Key</h2>
          {!addingProvider && (
            <Button variant="outline" size="sm" onClick={() => setAddingProvider(true)}>
              <Plus size={12} /> 新增 Provider
            </Button>
          )}
        </div>

        {addingProvider && (
          <div className="flex items-center gap-2 p-3 border border-dashed border-border rounded-lg bg-muted/20">
            <input
              autoFocus
              placeholder="provider id (如 moonshot)"
              value={newProviderId}
              onChange={e => setNewProviderId(e.target.value)}
              className="flex-1 px-2 py-1.5 text-sm font-mono border border-border rounded-md
                          focus:outline-none focus:ring-2 focus:ring-foreground/20"
            />
            <input
              placeholder="显示名 (如 Moonshot AI)"
              value={newProviderName}
              onChange={e => setNewProviderName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleAddProvider()}
              className="flex-1 px-2 py-1.5 text-sm border border-border rounded-md
                          focus:outline-none focus:ring-2 focus:ring-foreground/20"
            />
            <Button size="sm" onClick={handleAddProvider}
                    disabled={!newProviderId.trim() || !newProviderName.trim()}>
              <Check size={12} /> 添加
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setAddingProvider(false)}>
              <X size={12} />
            </Button>
          </div>
        )}

        {providers.map(p => {
          const stored = keys?.[p.id]
          return (
            <div key={p.id} className="grid grid-cols-[1fr_auto_auto] gap-2 items-center">
              <div>
                <div className="text-sm font-medium">{p.name}</div>
                <div className="text-xs text-muted-foreground font-mono">
                  {stored ? `已存:${stored}` : '(未配置)'}
                </div>
                <div className="flex items-center gap-1 mt-1">
                  <input
                    type={showKey[p.id] ? 'text' : 'password'}
                    placeholder="输入新 API key"
                    value={keyDraft[p.id] || ''}
                    onChange={e => setKeyDraft({ ...keyDraft, [p.id]: e.target.value })}
                    className="flex-1 px-2 py-1.5 text-sm font-mono border border-border rounded-md
                                focus:outline-none focus:ring-2 focus:ring-foreground/20"
                  />
                  <button
                    onClick={() => setShowKey({ ...showKey, [p.id]: !showKey[p.id] })}
                    className="p-1.5 text-muted-foreground hover:text-foreground"
                  >
                    {showKey[p.id] ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              <Button
                size="sm"
                disabled={!keyDraft[p.id] || saveKey.isPending}
                onClick={() => saveKey.mutate({ provider: p.id, api_key: keyDraft[p.id] })}
              >
                💾 保存
              </Button>
              <Button
                size="sm" variant="outline"
                disabled={!stored && !keyDraft[p.id]}
                onClick={() => testConn.mutate({
                  provider: p.id,
                  api_key: keyDraft[p.id] || undefined,
                })}
              >
                {testConn.isPending ? <Loader2 size={12} className="animate-spin" /> : '🩺'} 测连接
              </Button>
              {testResult[p.id] && (
                <div className={`col-span-3 text-xs px-2 py-1 rounded
                                  ${testResult[p.id]!.ok
                                    ? 'bg-success/10 text-success'
                                    : 'bg-destructive/10 text-destructive'}`}>
                  {testResult[p.id]!.msg}
                </div>
              )}
            </div>
          )
        })}
      </Card>

      {cur && (
        <Card className="p-5 space-y-4">
          <h2 className="text-sm font-semibold">🤖 模型配置（每步可独立指定）</h2>

          <div className="text-xs text-muted-foreground px-1 py-1 bg-muted/30 rounded">
            热路径（高频调用）用 flash 模型省成本，冷路径（评分/抽取）用 pro 模型保准确度
          </div>

          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider pt-1">
            热路径（每轮调用）
          </div>
          {(['sut', 'simulator'] as const).map(role => (
            <ModelRoleRow
              key={role} role={role} data={cur[role]}
              onChange={(next) => setModelDraft({ ...(modelDraft ?? cur), [role]: next })}
            />
          ))}

          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider pt-2">
            冷路径（评分）
          </div>
          <ModelRoleRow
            role="judge" data={cur.judge}
            onChange={(next) => setModelDraft({ ...(modelDraft ?? cur), judge: next })}
          />

          <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider pt-2">
            一次性步骤（任务设置 / 建议）
          </div>
          {STEP_KEYS.map(({ key, label }) => {
            const data = cur[key] ?? cur.judge
            return (
              <ModelRoleRow
                key={key} role={key} data={data}
                labels={{ [key]: label }}
                onChange={(next) => setModelDraft({ ...(modelDraft ?? cur), [key]: next })}
              />
            )
          })}

          <div className="grid grid-cols-2 gap-3 pt-2">
            <div>
              <label className="text-xs text-muted-foreground">默认并发数</label>
              <input
                type="number"
                value={cur.concurrency}
                onChange={e => setModelDraft({
                  ...(modelDraft ?? cur),
                  concurrency: parseInt(e.target.value) || 4,
                })}
                min={1} max={16}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-md text-sm
                            focus:outline-none focus:ring-2 focus:ring-foreground/20"
              />
            </div>
          </div>
          <div className="flex justify-end pt-2 border-t border-border">
            <Button
              variant="primary"
              disabled={!modelDraft || saveModels.isPending}
              onClick={() => saveModels.mutate(modelDraft)}
            >
              {saveModels.isPending && <Loader2 size={12} className="animate-spin" />}
              💾 保存模型配置
            </Button>
          </div>
        </Card>
      )}
    </div>
  )
}


const STEP_KEYS = [
  { key: 'extract_rubric' as const, label: '抽取 Rubric' },
  { key: 'extract_personas' as const, label: '抽取 Persona' },
  { key: 'extract_flow' as const, label: '抽取 Flow' },
  { key: 'extract_variables' as const, label: '抽取变量' },
  { key: 'recommend' as const, label: '改进建议' },
  { key: 'apply_patch' as const, label: '修改 Prompt' },
]

const DEFAULT_LABELS: Record<string, string> = {
  sut: 'SUT(被测模型)',
  simulator: '模拟器(用户)',
  judge: 'Judge(评委)',
  extract_rubric: '抽取 Rubric',
  extract_personas: '抽取 Persona',
  extract_flow: '抽取 Flow',
  extract_variables: '抽取变量',
  recommend: '改进建议',
  apply_patch: '修改 Prompt',
}

const DEFAULT_HINTS: Record<string, string> = {
  sut: '通常温度 0.7',
  simulator: '扮演用户,温度 0.7',
  judge: '温度 0,能力 ≥ SUT',
  extract_rubric: '从 Prompt 产评分项',
  extract_personas: '自动设计测试场景',
  extract_flow: '产流程图',
  extract_variables: '提取业务变量',
  recommend: '弱项分析',
  apply_patch: '按建议改 Prompt',
}

function ModelRoleRow({ role, data, onChange, labels }: {
  role: string
  data: any
  onChange: (next: any) => void
  labels?: Record<string, string>
}) {
  const allLabels = { ...DEFAULT_LABELS, ...labels }
  return (
    <div className="grid grid-cols-[180px_1fr_1fr_1fr] gap-2 items-end pb-3 border-b border-border/40">
      <div>
        <div className="text-sm font-medium">{allLabels[role] || role}</div>
        <div className="text-xs text-muted-foreground">
          {DEFAULT_HINTS[role] || ''}
        </div>
      </div>
      <div>
        <label className="text-xs text-muted-foreground">model</label>
        <input
          value={data.model}
          onChange={e => onChange({ ...data, model: e.target.value })}
          className="w-full mt-1 px-2 py-1 border border-border rounded text-sm font-mono
                      focus:outline-none focus:ring-1 focus:ring-foreground/30"
        />
      </div>
      <div>
        <label className="text-xs text-muted-foreground">temperature</label>
        <input
          type="number"
          value={data.temperature}
          onChange={e => onChange({ ...data, temperature: parseFloat(e.target.value) || 0 })}
          min={0} max={1} step={0.1}
          className="w-full mt-1 px-2 py-1 border border-border rounded text-sm
                      focus:outline-none focus:ring-1 focus:ring-foreground/30"
        />
      </div>
      <div>
        <label className="text-xs text-muted-foreground">reasoning_effort</label>
        <select
          value={data.reasoning_effort}
          onChange={e => onChange({ ...data, reasoning_effort: e.target.value })}
          className="w-full mt-1 px-2 py-1 border border-border rounded text-sm
                      focus:outline-none focus:ring-1 focus:ring-foreground/30"
        >
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </div>
    </div>
  )
}


// ============================ Persona 库 ============================

function PersonaSection() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['persona-library'],
    queryFn: PersonaLibAPI.get,
  })

  const [draft, setDraft] = useState<PersonaDimension[] | null>(null)
  const dims = draft ?? data?.dimensions ?? []
  const dirty = draft !== null

  const saveMut = useMutation({
    mutationFn: (d: PersonaDimension[]) => PersonaLibAPI.save(d),
    onSuccess: () => {
      setDraft(null)
      qc.invalidateQueries({ queryKey: ['persona-library'] })
    },
  })

  const updateDim = (idx: number, patch: Partial<PersonaDimension>) => {
    const next = dims.map((d, i) => i === idx ? { ...d, ...patch } : d)
    setDraft(next)
  }

  const addDimension = () => {
    setDraft([...dims, { dim: '', label: '', values: [] }])
  }

  const removeDimension = (idx: number) => {
    setDraft(dims.filter((_, i) => i !== idx))
  }

  const updateValue = (dimIdx: number, valIdx: number, patch: Partial<PersonaDimensionValue>) => {
    const nextVals = dims[dimIdx].values.map((v, i) => i === valIdx ? { ...v, ...patch } : v)
    updateDim(dimIdx, { values: nextVals })
  }

  const addValue = (dimIdx: number) => {
    const nextVals = [...dims[dimIdx].values, { value: '', label: '', desc: '', usage_count: 0 }]
    updateDim(dimIdx, { values: nextVals })
  }

  const removeValue = (dimIdx: number, valIdx: number) => {
    const nextVals = dims[dimIdx].values.filter((_, i) => i !== valIdx)
    updateDim(dimIdx, { values: nextVals })
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          维度属性字典(公用）。「新建测试」从这里选维度 + 配比例 → 独立采样生成 persona。
        </p>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={addDimension}>
            <Plus size={12} /> 新增维度
          </Button>
          {dirty && (
            <>
              <Button variant="ghost" size="sm" onClick={() => setDraft(null)}>
                撤销
              </Button>
              <Button
                variant="primary" size="sm"
                disabled={saveMut.isPending}
                onClick={() => saveMut.mutate(dims)}
              >
                {saveMut.isPending && <Loader2 size={12} className="animate-spin" />}
                保存
              </Button>
            </>
          )}
        </div>
      </div>

      {saveMut.isError && (
        <div className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded">
          保存失败：{(saveMut.error as Error)?.message || '未知错误'}
        </div>
      )}

      {dims.map((dim, di) => (
        <DimensionEditor
          key={di}
          dim={dim}
          onUpdateDim={(patch) => updateDim(di, patch)}
          onRemoveDim={() => removeDimension(di)}
          onUpdateValue={(vi, patch) => updateValue(di, vi, patch)}
          onAddValue={() => addValue(di)}
          onRemoveValue={(vi) => removeValue(di, vi)}
        />
      ))}
    </div>
  )
}


function DimensionEditor({ dim, onUpdateDim, onRemoveDim, onUpdateValue, onAddValue, onRemoveValue }: {
  dim: PersonaDimension
  onUpdateDim: (patch: Partial<PersonaDimension>) => void
  onRemoveDim: () => void
  onUpdateValue: (vi: number, patch: Partial<PersonaDimensionValue>) => void
  onAddValue: () => void
  onRemoveValue: (vi: number) => void
}) {
  const [collapsed, setCollapsed] = useState(false)
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const isAttitude = dim.dim === 'attitude'

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2.5 bg-muted/30">
        <button onClick={() => setCollapsed(!collapsed)}
                className="flex items-center gap-2">
          <ChevronDown size={14} className={`text-muted-foreground transition-transform
                                              ${collapsed ? '-rotate-90' : ''}`} />
          <h3 className="text-sm font-semibold">{dim.label || dim.dim || '(新维度)'}</h3>
          <span className="text-xs text-muted-foreground">{dim.values.length} 种</span>
        </button>
        <div className="flex items-center gap-1">
          {!collapsed && (
            <>
              <InlineEdit
                value={dim.dim}
                placeholder="key"
                mono
                onChange={v => onUpdateDim({ dim: v })}
              />
              <InlineEdit
                value={dim.label}
                placeholder="显示名"
                onChange={v => onUpdateDim({ label: v })}
              />
            </>
          )}
          <Button variant="ghost" size="sm" onClick={onRemoveDim}
                  className="text-destructive hover:text-destructive">
            <Trash2 size={12} />
          </Button>
        </div>
      </div>

      {!collapsed && (
        <div className="px-4 pb-3 pt-2 space-y-1">
          {dim.values.length === 0 && (
            <div className="text-xs text-muted-foreground py-2">暂无属性值</div>
          )}
          {dim.values.map((v, vi) => (
            <div key={vi}
                 className="group grid items-center gap-2 px-2 py-1.5 rounded
                            border border-border/60 hover:border-border transition-colors"
                 style={{ gridTemplateColumns: isAttitude ? '100px 80px 1fr 1fr 1fr auto' : '100px 80px 1fr auto' }}>
              {editingIdx === vi ? (
                <>
                  <input value={v.value}
                         onChange={e => onUpdateValue(vi, { value: e.target.value })}
                         placeholder="value"
                         className="px-1.5 py-0.5 text-xs font-mono border border-border rounded
                                    focus:outline-none focus:ring-1 focus:ring-foreground/30" />
                  <input value={v.label}
                         onChange={e => onUpdateValue(vi, { label: e.target.value })}
                         placeholder="显示名"
                         className="px-1.5 py-0.5 text-xs border border-border rounded
                                    focus:outline-none focus:ring-1 focus:ring-foreground/30" />
                  <input value={v.desc}
                         onChange={e => onUpdateValue(vi, { desc: e.target.value })}
                         placeholder="简述"
                         className="px-1.5 py-0.5 text-xs border border-border rounded
                                    focus:outline-none focus:ring-1 focus:ring-foreground/30" />
                  {isAttitude && (
                    <>
                      <input value={v.description || ''}
                             onChange={e => onUpdateValue(vi, { description: e.target.value })}
                             placeholder="自我描述"
                             className="px-1.5 py-0.5 text-xs border border-border rounded
                                        focus:outline-none focus:ring-1 focus:ring-foreground/30" />
                      <input value={v.speaking_style || ''}
                             onChange={e => onUpdateValue(vi, { speaking_style: e.target.value })}
                             placeholder="说话风格"
                             className="px-1.5 py-0.5 text-xs border border-border rounded
                                        focus:outline-none focus:ring-1 focus:ring-foreground/30" />
                    </>
                  )}
                  <button onClick={() => setEditingIdx(null)}
                          className="p-1 text-muted-foreground hover:text-foreground">
                    <Check size={12} />
                  </button>
                </>
              ) : (
                <>
                  <span className="font-mono text-xs font-medium truncate">{v.value}</span>
                  <span className="text-xs text-muted-foreground truncate">{v.label}</span>
                  <span className="text-xs text-muted-foreground truncate italic">
                    {v.desc}
                    {isAttitude && v.description && ` · ${v.description.slice(0, 20)}…`}
                  </span>
                  {isAttitude && <span />}
                  {isAttitude && <span />}
                  <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    {v.usage_count > 0 && (
                      <Badge variant="success" className="mr-1">{v.usage_count}</Badge>
                    )}
                    <button onClick={() => setEditingIdx(vi)}
                            className="p-1 text-muted-foreground hover:text-foreground">
                      <Pencil size={11} />
                    </button>
                    <button onClick={() => onRemoveValue(vi)}
                            className="p-1 text-muted-foreground hover:text-destructive">
                      <Trash2 size={11} />
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
          <button onClick={onAddValue}
                  className="flex items-center gap-1 text-xs text-muted-foreground
                             hover:text-foreground py-1.5 px-2 rounded hover:bg-accent transition-colors">
            <Plus size={11} /> 添加属性值
          </button>
        </div>
      )}
    </Card>
  )
}


function InlineEdit({ value, placeholder, mono, onChange }: {
  value: string; placeholder: string; mono?: boolean
  onChange: (v: string) => void
}) {
  const isNew = !value
  const [editing, setEditing] = useState(isNew)
  const [draft, setDraft] = useState(value)

  if (editing) {
    return (
      <div className="flex items-center gap-0.5">
        <input
          autoFocus
          value={draft}
          onChange={e => { setDraft(e.target.value); onChange(e.target.value) }}
          onBlur={() => setEditing(false)}
          onKeyDown={e => {
            if (e.key === 'Enter') setEditing(false)
            if (e.key === 'Escape') { setDraft(value); onChange(value); setEditing(false) }
          }}
          placeholder={placeholder}
          className={`px-1.5 py-0.5 text-xs border border-border rounded w-24
                      focus:outline-none focus:ring-1 focus:ring-foreground/30
                      ${mono ? 'font-mono' : ''}`}
        />
      </div>
    )
  }

  return (
    <button onClick={() => { setDraft(value); setEditing(true) }}
            className={`px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground
                        border border-transparent hover:border-border rounded transition-colors
                        ${mono ? 'font-mono' : ''}`}
            title={`编辑 ${placeholder}`}>
      {value || <span className="italic">{placeholder}</span>}
    </button>
  )
}


// ============================ 噪音库 ============================

function NoiseSection() {
  return (
    <Card className="p-5">
      <p className="text-sm text-muted-foreground">
        噪音种类定义(filler/asr_error/broken/interrupt)。
        当前仍可通过 Streamlit fallback 编辑(<code>claw-eval editor</code>)。
        React UI 下轮接入完整编辑。
      </p>
    </Card>
  )
}
