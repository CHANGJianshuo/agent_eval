import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, Zap, Shield, GitBranch, Route } from 'lucide-react'

import { TasksAPI, type ScriptInfo } from '@/lib/api'
import { Badge } from '@/components/ui/Badge'


interface Props {
  taskId: string
}


export function ScriptList({ taskId }: Props) {
  const { data } = useQuery({
    queryKey: ['scripts', taskId],
    queryFn: () => TasksAPI.scripts(taskId),
  })

  const scripts = data?.scripts ?? []

  // 统计所有剧本覆盖的 flow 节点
  const allCovered = new Set(scripts.flatMap(s => s.covers_flow_nodes))

  if (scripts.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-6 text-center">
        暂无剧本（生成任务后会自动产出）
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted-foreground">
        {scripts.length} 个剧本　·　每个剧本覆盖 flow 图的一条逻辑路径　·　与性格独立组合
      </div>

      {/* 覆盖率总览 */}
      {allCovered.size > 0 && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Route size={12} />
          <span>共覆盖 {allCovered.size} 个 flow 节点：</span>
          <div className="flex flex-wrap gap-1">
            {[...allCovered].map(n => (
              <span key={n} className="px-1.5 py-0.5 bg-accent rounded font-mono text-[10px]">{n}</span>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-2">
        {scripts.map(s => (
          <ScriptCard key={s.id} script={s} />
        ))}
      </div>

    </div>
  )
}


function ScriptCard({ script: s }: { script: ScriptInfo }) {
  const [expanded, setExpanded] = useState(false)
  const isV2 = !!s.scenario

  return (
    <div className={`border rounded-lg transition-colors
                      ${s.is_adversarial ? 'border-destructive/30 bg-destructive/3' : 'border-border'}`}>
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-2.5 flex items-center gap-3 text-left hover:bg-accent/30 transition-colors"
      >
        <ChevronDown size={14} className={`text-muted-foreground transition-transform shrink-0
                                            ${expanded ? '' : '-rotate-90'}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-medium">{s.id}</span>
            {s.name && s.name !== s.id && (
              <span className="text-xs text-muted-foreground">{s.name}</span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <GitBranch size={10} /> {s.covers_flow_nodes.length} 节点
            </span>
            {s.n_probes > 0 && (
              <span className="flex items-center gap-1">
                <Zap size={10} /> {s.n_probes} 探针
              </span>
            )}
            <span>max {s.max_rounds} 轮</span>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {s.is_draft && <Badge variant="warning">待审核</Badge>}
          {s.is_adversarial && <Badge variant="danger"><Shield size={10} className="inline mr-0.5" />对抗</Badge>}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-border/50 space-y-3">
          {/* 场景描述 */}
          {isV2 && (
            <div>
              <div className="text-xs font-semibold text-muted-foreground mb-1.5">场景描述</div>
              <div className="text-sm text-foreground/80 bg-accent/30 rounded px-3 py-2 whitespace-pre-wrap">
                {s.scenario}
              </div>
            </div>
          )}

          {/* v1 兼容:状态机 */}
          {!isV2 && Object.keys(s.states).length > 0 && (
            <div>
              <div className="text-xs font-semibold text-muted-foreground mb-1.5">
                状态机 <span className="font-normal">(v1 格式)</span>
              </div>
              <div className="flex flex-wrap items-center gap-1 mb-2">
                {buildFlowChain(s.initial_state, s.transitions, Object.keys(s.states)).map((node, i, arr) => (
                  <span key={i} className="flex items-center gap-1">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono
                                      ${node === s.initial_state
                                        ? 'bg-foreground text-background font-semibold'
                                        : node === 'END'
                                          ? 'bg-muted text-muted-foreground border border-border'
                                          : 'bg-accent border border-border'}`}>
                      {node}
                    </span>
                    {i < arr.length - 1 && <span className="text-muted-foreground">→</span>}
                  </span>
                ))}
              </div>
              <div className="space-y-1">
                {Object.entries(s.states).map(([name, instr]) => (
                  <div key={name} className="grid grid-cols-[100px_1fr] gap-2 text-xs">
                    <span className="font-mono text-muted-foreground truncate">{name}</span>
                    <span className="text-foreground/80">{instr}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 覆盖的 flow 节点 */}
          {s.covers_flow_nodes.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-muted-foreground mb-1">
                <Route size={10} className="inline mr-1" />覆盖路径
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {s.covers_flow_nodes.map((n, i) => (
                  <span key={n} className="flex items-center gap-1">
                    <Badge variant="outline">
                      <span className="font-mono text-[10px]">{n}</span>
                    </Badge>
                    {i < s.covers_flow_nodes.length - 1 && (
                      <span className="text-muted-foreground text-[10px]">→</span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* 探针 */}
          {s.probes.length > 0 && (
            <div>
              <div className="text-xs font-semibold text-muted-foreground mb-1.5">
                <Zap size={10} className="inline mr-1" />探针
              </div>
              <div className="space-y-1.5">
                {s.probes.map(p => (
                  <div key={p.id} className="border border-border/60 rounded px-3 py-2 text-xs space-y-0.5">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline">{p.id}</Badge>
                      <span className="text-muted-foreground">第 {p.inject_at_turn} 轮注入</span>
                      {p.description && (
                        <span className="text-muted-foreground">· {p.description}</span>
                      )}
                    </div>
                    <div className="text-foreground/80 italic">"{p.text}"</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}


function buildFlowChain(
  initial: string,
  transitions: Record<string, string | Record<string, number>>,
  allStates: string[],
): string[] {
  if (!initial) return allStates
  const chain: string[] = [initial]
  const visited = new Set([initial])
  let current = initial
  for (let i = 0; i < 20; i++) {
    const next = transitions[current]
    if (!next) break
    const target = typeof next === 'string' ? next : Object.keys(next)[0]
    if (!target) break
    chain.push(target)
    if (target === 'END' || visited.has(target)) break
    visited.add(target)
    current = target
  }
  for (const s of allStates) {
    if (!visited.has(s)) chain.push(s)
  }
  return chain
}
