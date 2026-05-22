import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'

import { TestsAPI } from '@/lib/api'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { MilestoneProgress } from '@/components/ui/Progress'


export default function TestDetail() {
  const { testId = '' } = useParams<{ testId: string }>()
  const { data: test } = useQuery({
    queryKey: ['test', testId],
    queryFn: () => TestsAPI.get(testId),
    enabled: !!testId,
  })

  if (!test) return <div>加载中…</div>

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to={`/tasks/${test.task_id}`}>
          <Button variant="ghost" size="md">
            <ArrowLeft size={14} /> {test.task_id}
          </Button>
        </Link>
        <h1 className="text-xl font-semibold font-mono">{testId}</h1>
      </div>

      <Card className="p-5">
        <div className="grid grid-cols-5 gap-6">
          {[
            { k: '状态', v: test.status },
            { k: '创建时间', v: test.created_at.slice(0, 16) },
            { k: 'agent', v: test.agent_version || '—' },
            { k: 'case', v: test.n_results },
            { k: '通过率', v: test.pass_rate == null ? '—' :
                                  `${Math.round(test.pass_rate * 100)}%` },
          ].map(it => (
            <div key={it.k}>
              <div className="text-xs text-muted-foreground">{it.k}</div>
              <div className="text-sm font-medium mt-1">{it.v}</div>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-5">
        <h2 className="text-sm font-semibold mb-3">进度</h2>
        <MilestoneProgress
          milestones={test.milestones}
          labels={['配置', '评测', '报告', '建议']}
        />
      </Card>

      <Card className="p-5">
        <h2 className="text-sm font-semibold mb-3">参数</h2>
        <pre className="text-xs font-mono bg-muted p-3 rounded-md overflow-auto">
          {JSON.stringify(test.params, null, 2)}
        </pre>
      </Card>

      <p className="text-sm text-muted-foreground">
        完整的报告嵌入 / 建议自动应用 / 回归对比 — 下轮交付。
      </p>
    </div>
  )
}
