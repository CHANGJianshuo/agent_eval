import { cn } from '@/lib/cn'

interface Props {
  milestones: { m1: boolean; m2: boolean; m3: boolean; m4: boolean }
  labels?: string[]
  className?: string
}

const DEFAULT_LABELS = ['评测方案', '模拟用户', '评测', '报告']

export function MilestoneProgress({ milestones, labels = DEFAULT_LABELS, className }: Props) {
  const ms = [milestones.m1, milestones.m2, milestones.m3, milestones.m4]
  const nextIdx = ms.findIndex(v => !v)

  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      {ms.map((done, i) => {
        const isCurrent = i === nextIdx
        return (
          <div key={i} className="flex items-center gap-1.5">
            <div
              className={cn(
                'inline-flex items-center gap-1 px-2.5 py-1 rounded-full',
                'text-xs font-medium border transition-colors',
                done && 'bg-success/10 text-success border-success/30',
                isCurrent && 'bg-warning/10 text-warning border-warning/30',
                !done && !isCurrent && 'bg-muted text-muted-foreground border-border',
              )}
            >
              <span className="font-mono text-[10px]">{i + 1}</span>
              <span>{labels[i]}</span>
              {done && <span>✓</span>}
            </div>
            {i < 3 && <span className="text-border">→</span>}
          </div>
        )
      })}
    </div>
  )
}
