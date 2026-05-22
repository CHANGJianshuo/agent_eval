/**
 * 任务级配置(显示在任务概览页 expander 内):
 *   - Prompt 编辑(textarea + 保存 + 备份为新版本)
 *   - Rubrics 表展示
 *   - 版本历史表
 */
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'

import { TasksAPI } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'


export function TaskConfig({ taskId }: { taskId: string }) {
  const [tab, setTab] = useState<'prompt' | 'rubrics' | 'versions'>('prompt')
  return (
    <div className="space-y-3">
      <div className="flex gap-1 border-b border-border">
        {([
          ['prompt', '📝 Prompt'],
          ['rubrics', '📐 Rubrics'],
          ['versions', '📜 版本历史'],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-3 py-1.5 text-sm font-medium border-b-2 -mb-px transition-colors
                        ${tab === key
                          ? 'border-foreground text-foreground'
                          : 'border-transparent text-muted-foreground hover:text-foreground'}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="pt-3">
        {tab === 'prompt' && <PromptEditor taskId={taskId} />}
        {tab === 'rubrics' && <RubricsTable taskId={taskId} />}
        {tab === 'versions' && <VersionsTable taskId={taskId} />}
      </div>
    </div>
  )
}


function PromptEditor({ taskId }: { taskId: string }) {
  const { data, refetch } = useQuery({
    queryKey: ['task-prompt', taskId],
    queryFn: () => TasksAPI.getPrompt(taskId),
  })
  const [draft, setDraft] = useState<string | null>(null)
  const qc = useQueryClient()
  const saveMut = useMutation({
    mutationFn: (prompt: string) =>
      TasksAPI.updatePrompt(taskId, prompt),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['task-prompt', taskId] })
      setDraft(null)
    },
  })

  const current = draft ?? data?.prompt ?? ''
  const dirty = data && draft !== null && draft !== data.prompt

  if (!data) return <div className="text-sm text-muted-foreground">加载中…</div>

  return (
    <div className="space-y-3">
      <textarea
        value={current}
        onChange={e => setDraft(e.target.value)}
        rows={20}
        className="w-full px-3 py-2 text-sm font-mono border border-border rounded-md
                    focus:outline-none focus:ring-2 focus:ring-foreground/20
                    min-h-[400px]"
      />

      <div className="flex items-center justify-between">
        <div className="text-xs text-muted-foreground">
          {data.prompt.length} 字符
          {dirty && <span className="text-warning ml-2">· 已修改未保存</span>}
        </div>
        <div className="flex items-center gap-2">
          {dirty && (
            <Button variant="ghost" onClick={() => setDraft(null)}>
              ↩ 撤销
            </Button>
          )}
          <Button
            variant="primary"
            disabled={!dirty || saveMut.isPending}
            onClick={() => saveMut.mutate(draft!)}
          >
            {saveMut.isPending && <Loader2 size={14} className="animate-spin" />}
            💾 保存
          </Button>
        </div>
      </div>

      {Object.keys(data.variables || {}).length > 0 && (
        <Card className="p-4 mt-3">
          <h3 className="text-sm font-semibold mb-2">业务变量</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted-foreground">
                <th className="text-left py-1 pr-3">变量</th>
                <th className="text-left py-1">默认值</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.variables).map(([k, v]) => (
                <tr key={k} className="border-t border-border/50">
                  <td className="py-1.5 pr-3 font-mono">{k}</td>
                  <td className="py-1.5 font-mono text-muted-foreground">
                    {String(v)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}


function RubricsTable({ taskId }: { taskId: string }) {
  const { data } = useQuery({
    queryKey: ['task-rubrics', taskId],
    queryFn: () => TasksAPI.rubrics(taskId),
  })

  if (!data) return <div className="text-sm text-muted-foreground">加载中…</div>
  if (data.rubrics.length === 0) {
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        还没有 rubrics(任务可能尚未完成生成步骤)
      </Card>
    )
  }

  return (
    <div className="space-y-2">
      <div className="text-xs text-muted-foreground">
        共 {data.rubrics.length} 条
        {data.is_draft && <Badge variant="warning" className="ml-2">草稿待审</Badge>}
      </div>
      <Card className="overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 border-b border-border">
            <tr>
              <th className="text-left px-3 py-2">id</th>
              <th className="text-left px-3 py-2">category</th>
              <th className="text-left px-3 py-2">dim</th>
              <th className="text-left px-3 py-2">method</th>
              <th className="text-right px-3 py-2">weight</th>
              <th className="text-center px-3 py-2">★</th>
              <th className="text-left px-3 py-2">check</th>
            </tr>
          </thead>
          <tbody>
            {data.rubrics.map((r: any, i: number) => (
              <tr key={i} className="border-b border-border/30">
                <td className="px-3 py-2 font-mono text-xs">{r.id}</td>
                <td className="px-3 py-2 text-muted-foreground text-xs">
                  {r.category || '—'}
                </td>
                <td className="px-3 py-2 text-muted-foreground text-xs">
                  {r.dimension}
                </td>
                <td className="px-3 py-2 text-muted-foreground text-xs">
                  {r.method}
                </td>
                <td className="px-3 py-2 text-right text-xs">{r.weight}</td>
                <td className="px-3 py-2 text-center text-warning">
                  {r.is_safety ? '★' : ''}
                </td>
                <td className="px-3 py-2 text-xs text-muted-foreground truncate max-w-md">
                  {r.check}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}


function VersionsTable({ taskId }: { taskId: string }) {
  const { data } = useQuery({
    queryKey: ['task-versions', taskId],
    queryFn: () => TasksAPI.versions(taskId),
  })

  if (!data) return <div className="text-sm text-muted-foreground">加载中…</div>
  if (data.versions.length === 0) {
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        还没有版本。改动 Prompt 后保存会自动备份(下次完整版接入)。
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden p-0">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 border-b border-border">
          <tr>
            <th className="text-left px-3 py-2">label</th>
            <th className="text-left px-3 py-2">时间</th>
            <th className="text-left px-3 py-2">基于</th>
            <th className="text-left px-3 py-2">应用建议</th>
            <th className="text-left px-3 py-2">备注</th>
          </tr>
        </thead>
        <tbody>
          {[...data.versions].reverse().map((v: any) => (
            <tr key={v.label} className="border-b border-border/30">
              <td className="px-3 py-2 font-mono text-xs font-semibold">{v.label}</td>
              <td className="px-3 py-2 text-xs text-muted-foreground">
                {v.created_at?.slice(0, 16)}
              </td>
              <td className="px-3 py-2 text-xs text-muted-foreground font-mono">
                {v.based_on || '—'}
              </td>
              <td className="px-3 py-2 text-xs">
                {v.applied_recs?.length > 0 ?
                  v.applied_recs.map((r: string) =>
                    <Badge key={r} variant="default">{r}</Badge>) :
                  '—'}
              </td>
              <td className="px-3 py-2 text-xs text-muted-foreground">
                {v.note || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}
