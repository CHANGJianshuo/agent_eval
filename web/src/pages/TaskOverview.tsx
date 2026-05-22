import { useParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'

import { TasksAPI, TestsAPI } from '@/lib/api'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { MilestoneProgress } from '@/components/ui/Progress'


export default function TaskOverview() {
  const { taskId = '' } = useParams<{ taskId: string }>()
  const { data: task } = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => TasksAPI.get(taskId),
    enabled: !!taskId,
  })
  const { data: tests = [] } = useQuery({
    queryKey: ['tests', taskId],
    queryFn: () => TestsAPI.listByTask(taskId),
    enabled: !!taskId,
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to="/">
          <Button variant="ghost" size="md">
            <ArrowLeft size={14} /> 任务列表
          </Button>
        </Link>
        <h1 className="text-xl font-semibold font-mono">{taskId}</h1>
        {task && task.description && (
          <span className="text-sm text-muted-foreground">{task.description}</span>
        )}
      </div>

      <Card className="px-5 py-4 space-y-3">
        <h2 className="text-sm font-semibold text-muted-foreground">任务级配置(下轮交付):</h2>
        <p className="text-sm text-muted-foreground">
          完整的「任务概览」页(测试列表 + 新建测试 + 任务级配置)需要 Phase 3 实现。
          本轮 Phase 1+2 主要给出任务列表 + 后端 API 框架。
        </p>
        <p className="text-sm text-muted-foreground">
          当前任务下有 {tests.length} 个测试。
        </p>
        {task && (
          <div className="flex items-center gap-2 flex-wrap pt-1">
            <Badge>{task.n_rubrics} rubric</Badge>
            <Badge>{task.n_personas} persona</Badge>
            {task.n_adv_personas > 0 && (
              <Badge variant="danger">{task.n_adv_personas} 对抗</Badge>
            )}
            <Badge>v{task.n_versions}</Badge>
          </div>
        )}
      </Card>

      <Card>
        <div className="px-5 py-4 border-b border-border">
          <h2 className="text-sm font-semibold">测试历史</h2>
        </div>
        <div className="divide-y divide-border">
          {tests.length === 0 ? (
            <div className="px-5 py-8 text-sm text-muted-foreground text-center">
              还没有测试
            </div>
          ) : tests.map(t => (
            <Link
              key={t.test_id}
              to={`/tests/${t.test_id}`}
              className="block px-5 py-3 hover:bg-accent/50 transition-colors"
            >
              <div className="flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-sm font-medium">{t.test_id}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {t.created_at.slice(0, 16)} · agent: {t.agent_version || '—'}
                    · {t.n_results} case
                  </div>
                  <div className="mt-2">
                    <MilestoneProgress
                      milestones={t.milestones}
                      labels={['配置', '评测', '报告', '建议']}
                    />
                  </div>
                </div>
                <div className="text-right ml-4">
                  <div className="text-2xl font-semibold">
                    {t.pass_rate == null ? '—' : `${Math.round(t.pass_rate * 100)}%`}
                  </div>
                  <div className="text-xs text-muted-foreground">通过率</div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </Card>
    </div>
  )
}
