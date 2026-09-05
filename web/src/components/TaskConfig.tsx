import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Button } from './ui/Button'
import { Card } from './ui/Card'

export function errorText(error: any): string {
  const detail = error?.response?.data?.detail ?? error?.message ?? error
  return typeof detail === 'string' ? detail : JSON.stringify(detail)
}
export function ErrorLine({ error }: { error: any }) {
  return error ? (
    <p role="alert" className="text-sm text-destructive whitespace-pre-wrap">
      {errorText(error)}
    </p>
  ) : null
}
const input = 'w-full border border-border rounded px-2 py-1.5 text-sm bg-background'

export function TaskConfig({ taskId }: { taskId: string }) {
  const [tab, setTab] = useState('prompt')
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: ['configuration', taskId],
    queryFn: () => api.get(`/tasks/${taskId}/configuration`).then((r) => r.data),
  })
  const checks = useQuery({
    queryKey: ['configuration-check', taskId],
    queryFn: () => api.get(`/tasks/${taskId}/validate`).then((r) => r.data),
  })
  const save = useMutation({
    mutationFn: (files: Record<string, string | null>) =>
      api.put(`/tasks/${taskId}/configuration`, { files, expected_revision: query.data.revision }),
    onSuccess: () => qc.invalidateQueries(),
  })
  if (query.error) return <ErrorLine error={query.error} />
  if (!query.data) return <p>加载配置…</p>
  const files: Record<string, string> = query.data.files
  const revision = query.data.revision
  return (
    <div className="space-y-4">
      <div className="flex gap-2 flex-wrap border-b pb-2">
        {[
          ['prompt', 'Prompt 与变量'],
          ['rubrics', '评分项'],
          ['scripts', '剧本与探针'],
          ['sampling', '采样与噪音'],
          ['flow', '流程图'],
          ['versions', '版本与恢复'],
        ].map(([key, label]) => (
          <Button key={key} variant={tab === key ? 'primary' : 'ghost'} onClick={() => setTab(key)}>
            {label}
          </Button>
        ))}
      </div>
      <ErrorLine error={save.error} />
      {checks.data?.issues
        ?.filter((i: any) => i.level !== 'info')
        .map((i: any, n: number) => (
          <p
            key={n}
            className={`text-xs ${i.level === 'error' ? 'text-destructive' : 'text-warning'}`}
          >
            {i.message}
          </p>
        ))}
      {tab === 'prompt' && (
        <PromptForm
          key={revision}
          taskId={taskId}
          disabled={save.isPending}
          save={(data) => save.mutate({ 'task.yaml': JSON.stringify(data, null, 2) })}
        />
      )}
      {tab === 'rubrics' && (
        <StructuredEditor
          key={revision}
          taskId={taskId}
          names={Object.keys(files).filter((f) => f.startsWith('rubrics'))}
          initial="rubrics.yaml"
          disabled={save.isPending}
          save={(files) => save.mutate(files)}
          kind="rubrics"
        />
      )}
      {tab === 'scripts' && (
        <StructuredEditor
          key={revision}
          taskId={taskId}
          names={Object.keys(files).filter((f) => f.startsWith('personas'))}
          disabled={save.isPending}
          save={(files) => save.mutate(files)}
          kind="scripts"
        />
      )}
      {tab === 'sampling' && (
        <StructuredEditor
          key={revision}
          taskId={taskId}
          names={['sampling.yaml']}
          disabled={save.isPending}
          save={(files) => save.mutate(files)}
          kind="sampling"
        />
      )}
      {tab === 'flow' && (
        <StructuredEditor
          key={revision}
          taskId={taskId}
          names={['flow.yaml']}
          disabled={save.isPending}
          save={(files) => save.mutate(files)}
          kind="flow"
        />
      )}
      {tab === 'versions' && <Versions taskId={taskId} />}
    </div>
  )
}

function PromptForm({
  taskId,
  save,
  disabled,
}: {
  taskId: string
  save: (d: any) => void
  disabled: boolean
}) {
  const { data, error } = useQuery({
    queryKey: ['task-document', taskId],
    queryFn: () => api.get(`/tasks/${taskId}/documents`).then((r) => r.data),
  })
  if (error) return <ErrorLine error={error} />
  if (!data) return <p>加载中…</p>
  return (
    <PromptFields
      key={JSON.stringify(data.documents['task.yaml'])}
      value={data.documents['task.yaml']}
      save={save}
      disabled={disabled}
    />
  )
}
function PromptFields({
  value,
  save,
  disabled,
}: {
  value: any
  save: (d: any) => void
  disabled: boolean
}) {
  const [prompt, setPrompt] = useState(value.prompt)
  const [variables, setVariables] = useState<Record<string, any>>(value.variables || {})
  const [newName, setNewName] = useState('')
  const [desc, setDesc] = useState(value.description || '')
  const parseValue = (text: string) => {
    try {
      return JSON.parse(text)
    } catch {
      return text
    }
  }
  const dirty =
    prompt !== value.prompt ||
    desc !== (value.description || '') ||
    JSON.stringify(variables) !== JSON.stringify(value.variables || {})
  return (
    <div className="space-y-3">
      <label className="block text-sm">
        任务说明
        <input
          aria-label="任务说明"
          className={input}
          value={desc}
          onChange={(e) => setDesc(e.target.value)}
        />
      </label>
      <label className="block text-sm">
        Prompt
        <textarea
          aria-label="任务 Prompt"
          className={`${input} font-mono min-h-[300px]`}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
        />
      </label>
      <p className="text-sm font-medium">业务变量</p>
      {Object.entries(variables).map(([name, value]) => (
        <div key={name} className="flex gap-2 items-center">
          <code className="w-36 shrink-0 text-xs">{name}</code>
          <input
            aria-label={`变量 ${name}`}
            className={input}
            value={typeof value === 'object' ? JSON.stringify(value) : String(value)}
            onChange={(e) => setVariables({ ...variables, [name]: parseValue(e.target.value) })}
          />
          <Button
            variant="ghost"
            onClick={() =>
              setVariables(
                Object.fromEntries(Object.entries(variables).filter(([key]) => key !== name)),
              )
            }
          >
            删除
          </Button>
        </div>
      ))}
      <div className="flex gap-2">
        <input
          aria-label="新变量名"
          placeholder="新变量名"
          className={input}
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <Button
          variant="outline"
          disabled={!newName.trim() || newName in variables}
          onClick={() => {
            setVariables({ ...variables, [newName.trim()]: '' })
            setNewName('')
          }}
        >
          添加变量
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        数值会保存为数字，其他文本保留原值。保存会校验变量引用并备份修改前后的完整任务配置。
      </p>
      <Button
        variant="primary"
        disabled={disabled || !dirty}
        onClick={() => save({ ...value, prompt, description: desc, variables })}
      >
        {disabled ? '保存中…' : '保存并创建版本'}
      </Button>
    </div>
  )
}

function StructuredEditor({
  taskId,
  names,
  initial,
  save,
  disabled,
  kind,
}: {
  taskId: string
  names: string[]
  initial?: string
  save: (f: Record<string, string | null>) => void
  disabled: boolean
  kind: string
}) {
  const [name, setName] = useState(names.includes(initial || '') ? initial! : names[0] || '')
  const [newId, setNewId] = useState('')
  const [newDocument, setNewDocument] = useState<any>(null)
  const { data, error } = useQuery({
    queryKey: ['task-document', taskId],
    queryFn: () => api.get(`/tasks/${taskId}/documents`).then((r) => r.data),
  })
  if (error) return <ErrorLine error={error} />
  if (!data) return <p>加载中…</p>
  const value =
    newDocument ??
    data.documents[name] ??
    (kind === 'flow'
      ? { nodes: [], edges: [] }
      : kind === 'sampling'
        ? { weights: {}, noise_overlay: { rate: 0, kinds: [] } }
        : { rubrics: [] })
  return (
    <div className="space-y-3">
      {names.length > 0 && (
        <select
          aria-label="配置文件"
          className={input}
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setNewDocument(null)
          }}
        >
          {names.map((n) => (
            <option key={n}>{n}</option>
          ))}
          {name && !names.includes(name) && <option>{name}</option>}
        </select>
      )}
      {kind === 'scripts' && (
        <div className="flex gap-2">
          <input
            aria-label="新剧本 ID"
            className={input}
            placeholder="新剧本 ID（字母、数字或下划线）"
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
          />
          <Button
            variant="outline"
            disabled={
              !/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(newId) ||
              names.some((n) => n.endsWith(`/${newId}.yaml`))
            }
            onClick={() => {
              setName(`personas_draft/${newId}.yaml`)
              setNewDocument({
                id: newId,
                name: newId,
                scenario: '',
                max_rounds: 12,
                probes: [],
                covers_flow_nodes: [],
                noise: { rate: 0, kinds: [] },
              })
            }}
          >
            新增剧本草稿
          </Button>
        </div>
      )}
      {name && (
        <DocumentFields
          key={name + JSON.stringify(value)}
          name={name}
          value={value}
          kind={kind}
          disabled={disabled}
          save={(text) => save({ [name]: text })}
          remove={() => save({ [name]: null })}
        />
      )}
      {!name && <p className="text-sm text-muted-foreground">暂无配置，请先生成任务或新增剧本。</p>}
    </div>
  )
}
function DocumentFields({
  name,
  value,
  kind,
  disabled,
  save,
  remove,
}: {
  name: string
  value: any
  kind: string
  disabled: boolean
  save: (s: string) => void
  remove: () => void
}) {
  const [draft, setDraft] = useState(JSON.stringify(value, null, 2))
  const [confirmDelete, setConfirmDelete] = useState(false)
  let parsed: any,
    error = ''
  try {
    parsed = JSON.parse(draft)
  } catch {
    error = 'JSON 格式错误，请检查引号和逗号'
  }
  const patch = (field: string, next: any) =>
    setDraft(JSON.stringify({ ...parsed, [field]: next }, null, 2))
  const updateRubric = (i: number, changes: any) =>
    patch(
      'rubrics',
      parsed.rubrics.map((r: any, n: number) => (n === i ? { ...r, ...changes } : r)),
    )
  return (
    <div className="space-y-3">
      {kind === 'rubrics' &&
        Array.isArray(parsed?.rubrics) &&
        parsed.rubrics.map((r: any, i: number) => (
          <Card key={i} className="p-3 space-y-2">
            <div className="flex gap-2">
              <input
                aria-label={`评分项 ${i + 1} ID`}
                className={input}
                value={r.id}
                onChange={(e) => updateRubric(i, { id: e.target.value })}
              />
              <input
                aria-label={`评分项 ${i + 1} 权重`}
                type="number"
                min={0}
                step={0.05}
                className={`${input} max-w-24`}
                value={r.weight}
                onChange={(e) => updateRubric(i, { weight: Number(e.target.value) })}
              />
              <Button
                variant="ghost"
                onClick={() =>
                  patch(
                    'rubrics',
                    parsed.rubrics.filter((_: any, n: number) => n !== i),
                  )
                }
              >
                删除
              </Button>
            </div>
            <textarea
              aria-label={`评分项 ${i + 1} 检查标准`}
              className={input}
              rows={3}
              value={r.check}
              onChange={(e) => updateRubric(i, { check: e.target.value })}
            />
            <div className="flex gap-2">
              <select
                className={input}
                value={r.method}
                onChange={(e) => updateRubric(i, { method: e.target.value })}
              >
                {[
                  'llm_judge',
                  'keyword',
                  'length',
                  'placeholder',
                  'number_whitelist',
                  'ordered_keyword',
                  'pace_checker',
                  'blacklist',
                ].map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
              <select
                className={input}
                value={r.dimension}
                onChange={(e) =>
                  updateRubric(i, {
                    dimension: e.target.value,
                    is_safety: e.target.value === 'safety' || r.is_safety,
                  })
                }
              >
                {['completion', 'robustness', 'safety'].map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
              <label className="text-xs flex items-center gap-1 whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={!!r.is_safety}
                  onChange={(e) => updateRubric(i, { is_safety: e.target.checked })}
                />
                安全项
              </label>
            </div>
          </Card>
        ))}
      {kind === 'rubrics' && parsed && (
        <Button
          variant="outline"
          onClick={() =>
            patch('rubrics', [
              ...(parsed.rubrics || []),
              {
                id: 'completion.new_check',
                dimension: 'completion',
                method: 'llm_judge',
                weight: 1,
                check: '请填写可判定的检查标准',
              },
            ])
          }
        >
          添加评分项
        </Button>
      )}
      {kind === 'scripts' && parsed && (
        <>
          <label className="block text-sm">
            剧本名称
            <input
              className={input}
              value={parsed.name || ''}
              onChange={(e) => patch('name', e.target.value)}
            />
          </label>
          <label className="block text-sm">
            场景
            <textarea
              aria-label="场景"
              className={input}
              rows={5}
              value={parsed.scenario || ''}
              onChange={(e) => patch('scenario', e.target.value)}
            />
          </label>
          <label className="block text-sm">
            最大用户轮数
            <input
              className={input}
              type="number"
              min={1}
              max={100}
              value={parsed.max_rounds || 12}
              onChange={(e) => patch('max_rounds', Number(e.target.value))}
            />
          </label>
        </>
      )}
      <details open={kind === 'flow' || kind === 'sampling'}>
        <summary className="text-sm cursor-pointer">
          完整配置：触发条件、参数、探针与覆盖节点（JSON）
        </summary>
        <textarea
          aria-label="完整配置 JSON"
          className={`${input} font-mono mt-2`}
          rows={18}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
      </details>
      <ErrorLine error={error} />
      <div className="flex gap-2">
        <Button
          variant="primary"
          disabled={disabled || !!error || draft === JSON.stringify(value, null, 2)}
          onClick={() => save(draft)}
        >
          校验并保存版本
        </Button>
        {kind === 'scripts' && (
          <Button
            variant="outline"
            disabled={disabled}
            onClick={() => (confirmDelete ? remove() : setConfirmDelete(true))}
          >
            {confirmDelete ? `确认删除 ${name}` : '删除剧本'}
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        草稿需回到任务页审核后转正。结构化参数会在保存时校验，历史运行继续使用原快照。
      </p>
    </div>
  )
}

function Versions({ taskId }: { taskId: string }) {
  const qc = useQueryClient()
  const [label, setLabel] = useState('')
  const versions = useQuery({
    queryKey: ['task-versions', taskId],
    queryFn: () => api.get(`/tasks/${taskId}/versions`).then((r) => r.data),
  })
  const detail = useQuery({
    queryKey: ['version-detail', taskId, label],
    queryFn: () => api.get(`/tasks/${taskId}/versions/${label}`).then((r) => r.data),
    enabled: !!label,
  })
  const restore = useMutation({
    mutationFn: () =>
      api.post(`/tasks/${taskId}/versions/${label}/restore`, {
        expected_revision: detail.data.revision,
      }),
    onSuccess: () => {
      setLabel('')
      qc.invalidateQueries()
    },
  })
  return (
    <div className="space-y-3">
      <p className="text-sm">选择历史版本查看与当前配置的差异，恢复前会自动备份。</p>
      <select
        aria-label="历史版本"
        className={input}
        value={label}
        onChange={(e) => setLabel(e.target.value)}
      >
        <option value="">选择版本</option>
        {[...(versions.data?.versions || [])].reverse().map((v: any) => (
          <option key={v.label} value={v.label}>
            {v.label} · {v.note}
          </option>
        ))}
      </select>
      <ErrorLine error={versions.error || detail.error || restore.error} />
      {detail.data && (
        <>
          <p className="text-xs">
            {detail.data.complete_snapshot
              ? '完整任务配置快照'
              : '旧版本仅保存 Prompt，其他配置保持当前值'}
          </p>
          <pre className="text-xs bg-muted p-3 overflow-auto max-h-96 whitespace-pre-wrap">
            {detail.data.diff || '与当前配置一致'}
          </pre>
          <Button
            variant="primary"
            disabled={restore.isPending || !detail.data.diff.trim()}
            onClick={() => restore.mutate()}
          >
            恢复此版本并备份当前配置
          </Button>
        </>
      )}
    </div>
  )
}
