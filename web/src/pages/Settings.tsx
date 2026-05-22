import { useQuery } from '@tanstack/react-query'
import { PersonaLibAPI } from '@/lib/api'
import { Card } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'


export default function Settings() {
  const { data } = useQuery({
    queryKey: ['persona-library'],
    queryFn: PersonaLibAPI.get,
  })

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">全局配置</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          跨任务复用的资源(Persona 库 / 模型 / API key)。下轮完整版会含模型/key 编辑。
        </p>
      </div>

      <section className="space-y-4">
        <h2 className="text-sm font-semibold">🎭 Persona 库 · 5 个并列维度</h2>
        {data?.dimensions.map(dim => (
          <Card key={dim.dim} className="p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold">{dim.label}</h3>
              <span className="text-xs text-muted-foreground">
                {dim.values.length} 种
              </span>
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
                  {v.label && v.label !== v.value && (
                    <span className="text-muted-foreground">{v.label}</span>
                  )}
                  {v.desc && (
                    <span className="text-muted-foreground italic">
                      · {v.desc}
                    </span>
                  )}
                  {v.usage_count > 0 && (
                    <Badge variant="success">{v.usage_count} 在用</Badge>
                  )}
                </div>
              ))}
            </div>
          </Card>
        ))}
      </section>
    </div>
  )
}
