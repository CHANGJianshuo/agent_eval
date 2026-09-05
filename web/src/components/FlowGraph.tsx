import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TasksAPI, TestsAPI, type FlowNode } from '@/lib/api'


interface Props {
  taskId: string
  testId?: string
}

const NODE_W = 140
const NODE_H = 40
const GAP_X = 40
const GAP_Y = 56
const PAD = 20


export function FlowGraph({ taskId, testId }: Props) {
  const { data, error } = useQuery({
    queryKey: ['flow', taskId, testId],
    queryFn: () => testId ? TestsAPI.flow(testId) : TasksAPI.flow(taskId),
  })

  const layout = useMemo(() => {
    if (!data || data.nodes.length === 0) return null
    return computeLayout(data.nodes, data.edges)
  }, [data])

  if (error) return <p className="text-xs text-destructive">流程读取失败：{error.message}</p>
  if (!layout) return <p className="text-xs text-muted-foreground">没有可用的流程数据{testId ? '（历史运行可能没有保存快照）' : ''}</p>

  const { positions, width, height, edges } = layout

  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} className="block" style={{ minWidth: width }}>
        <defs>
          <marker id="arrow" markerWidth="6" markerHeight="6"
                  refX="5" refY="3" orient="auto">
            <path d="M0,0 L6,3 L0,6" fill="none" stroke="#a1a1aa" strokeWidth="1" />
          </marker>
        </defs>

        {edges.map((e, i) => {
          const from = positions[e[0]]
          const to = positions[e[1]]
          if (!from || !to) return null
          const x1 = from.x + NODE_W
          const y1 = from.y + NODE_H / 2
          const x2 = to.x
          const y2 = to.y + NODE_H / 2
          const isDashed = to.optional

          if (Math.abs(y1 - y2) < 2) {
            return (
              <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                    stroke="#a1a1aa" strokeWidth="1.5"
                    strokeDasharray={isDashed ? '4 3' : 'none'}
                    markerEnd="url(#arrow)" />
            )
          }
          const midX = (x1 + x2) / 2
          return (
            <path key={i}
                  d={`M${x1},${y1} C${midX},${y1} ${midX},${y2} ${x2},${y2}`}
                  fill="none" stroke="#a1a1aa" strokeWidth="1.5"
                  strokeDasharray={isDashed ? '4 3' : 'none'}
                  markerEnd="url(#arrow)" />
          )
        })}

        {data!.nodes.map(n => {
          const pos = positions[n.id]
          if (!pos) return null
          return (
            <g key={n.id}>
              <rect
                x={pos.x} y={pos.y}
                width={NODE_W} height={NODE_H}
                rx={6}
                fill={n.optional ? '#f4f4f5' : '#e4e4e7'}
                stroke={n.optional ? '#a1a1aa' : '#71717a'}
                strokeWidth={1.5}
                strokeDasharray={n.optional ? '4 3' : 'none'}
              />
              <text
                x={pos.x + NODE_W / 2} y={pos.y + 16}
                textAnchor="middle"
                fill="#18181b"
                fontSize={11}
                fontFamily="ui-monospace, monospace"
                fontWeight={600}
              >
                {n.id}
              </text>
              <text
                x={pos.x + NODE_W / 2} y={pos.y + 30}
                textAnchor="middle"
                fill="#71717a"
                fontSize={9}
              >
                {n.label.length > 12 ? n.label.slice(0, 11) + '…' : n.label}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}


interface Pos {
  x: number; y: number; col: number; row: number; optional?: boolean
}

function computeLayout(nodes: FlowNode[], edges: string[][]) {
  const inEdges = new Map<string, string[]>()
  for (const [a, b] of edges) {
    inEdges.set(b, [...(inEdges.get(b) || []), a])
  }

  const mainNodes = nodes.filter(n => !n.optional)
  const optNodes = nodes.filter(n => n.optional)

  const positions: Record<string, Pos> = {}
  mainNodes.forEach((n, i) => {
    positions[n.id] = { x: PAD + i * (NODE_W + GAP_X), y: PAD, col: i, row: 0 }
  })

  let branchRow = 1
  for (const n of optNodes) {
    const sources = (inEdges.get(n.id) || []).filter(s => positions[s])
    const refCol = sources.length > 0
      ? Math.min(...sources.map(s => positions[s].col))
      : 0
    positions[n.id] = {
      x: PAD + refCol * (NODE_W + GAP_X),
      y: PAD + branchRow * (NODE_H + GAP_Y),
      col: refCol, row: branchRow, optional: true,
    }
    branchRow++
  }

  const maxCol = Math.max(...Object.values(positions).map(p => p.col), 0)
  const maxRow = Math.max(...Object.values(positions).map(p => p.row), 0)
  const width = PAD * 2 + (maxCol + 1) * NODE_W + maxCol * GAP_X
  const height = PAD * 2 + (maxRow + 1) * NODE_H + maxRow * GAP_Y

  return { positions, width, height, edges }
}
