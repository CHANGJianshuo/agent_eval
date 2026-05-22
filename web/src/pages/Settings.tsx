import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Eye, EyeOff } from 'lucide-react'

import { ConfigAPI, PersonaLibAPI } from '@/lib/api'
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

  const PROVIDERS = [
    { id: 'xiaomi_mimo', name: '小米 MiMo' },
    { id: 'openai', name: 'OpenAI' },
    { id: 'anthropic', name: 'Anthropic' },
  ]

  return (
    <div className="space-y-5">
      <Card className="p-5 space-y-4">
        <h2 className="text-sm font-semibold">🔑 API Key</h2>
        {PROVIDERS.map(p => {
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
          <h2 className="text-sm font-semibold">🤖 三个模型角色</h2>
          {(['sut', 'simulator', 'judge'] as const).map(role => (
            <ModelRoleRow
              key={role} role={role} data={cur[role]}
              onChange={(next) => setModelDraft({ ...(modelDraft ?? cur), [role]: next })}
            />
          ))}
          <div className="grid grid-cols-2 gap-3">
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


function ModelRoleRow({ role, data, onChange }: {
  role: 'sut' | 'simulator' | 'judge'
  data: any
  onChange: (next: any) => void
}) {
  const labels = {
    sut: 'SUT(被测模型)',
    simulator: '模拟器(用户)',
    judge: 'Judge(评委)',
  }
  return (
    <div className="grid grid-cols-[180px_1fr_1fr_1fr] gap-2 items-end pb-3 border-b border-border/40">
      <div>
        <div className="text-sm font-medium">{labels[role]}</div>
        <div className="text-xs text-muted-foreground">
          {role === 'judge' && '温度 0,能力 ≥ SUT'}
          {role === 'sut' && '通常温度 0.7'}
          {role === 'simulator' && '扮演用户,温度 0.7'}
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
  const { data } = useQuery({
    queryKey: ['persona-library'],
    queryFn: PersonaLibAPI.get,
  })
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        5 个并列维度的属性字典。任务的「新建测试」勾选这些属性 + 配比例 → 系统采样生成 persona。
      </p>
      {data?.dimensions.map(dim => (
        <Card key={dim.dim} className="p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold">{dim.label}</h3>
            <span className="text-xs text-muted-foreground">{dim.values.length} 种</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {dim.values.map(v => (
              <div
                key={v.value}
                className="inline-flex items-center gap-2 px-2.5 py-1
                            border border-border rounded-md
                            text-xs hover:bg-accent transition-colors"
              >
                <span className="font-mono font-medium">{v.value}</span>
                {v.desc && (
                  <span className="text-muted-foreground italic">· {v.desc}</span>
                )}
                {v.usage_count > 0 && (
                  <Badge variant="success">{v.usage_count} 在用</Badge>
                )}
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
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
