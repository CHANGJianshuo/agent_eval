import { useState, useRef, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MessageSquare, X, Send, Loader2 } from 'lucide-react'

import { api } from '@/lib/api'
import { Button } from '@/components/ui/Button'


interface Message {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  taskId: string
}


export function AgentChatToggle({ taskId }: Props) {
  const [open, setOpen] = useState(false)

  return (
    <>
      <Button
        variant="outline" size="sm"
        onClick={() => setOpen(true)}
        className="gap-1.5"
      >
        <MessageSquare size={13} /> AI 修改助手
      </Button>
      {open && <AgentPanel taskId={taskId} onClose={() => setOpen(false)} />}
    </>
  )
}


function AgentPanel({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content: `你好！我可以帮你修改「${taskId}」的配置。例如：\n\n` +
        '- "把 opening 的权重调高到 0.15"\n' +
        '- "增加一条 safety rubric：不允许透露内部价格"\n' +
        '- "happy_path 剧本加一个探针，第 3 轮问退费政策"\n' +
        '- "删掉 constraint.length 这条评分项"\n\n' +
        '告诉我你想改什么。',
    },
  ])
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [messages])

  const chatMut = useMutation({
    mutationFn: async (userMsg: string) => {
      const res = await api.post(`/tasks/${taskId}/agent-chat`, {
        messages: [...messages, { role: 'user', content: userMsg }],
      })
      return res.data as { reply: string; applied: boolean }
    },
    onSuccess: (data, userMsg) => {
      setMessages(prev => [
        ...prev,
        { role: 'user', content: userMsg },
        { role: 'assistant', content: data.reply },
      ])
      if (data.applied) {
        qc.invalidateQueries({ queryKey: ['task-rubrics', taskId] })
        qc.invalidateQueries({ queryKey: ['scripts', taskId] })
        qc.invalidateQueries({ queryKey: ['flow', taskId] })
        qc.invalidateQueries({ queryKey: ['task', taskId] })
        qc.invalidateQueries({ queryKey: ['review-status', taskId] })
      }
    },
  })

  const handleSend = () => {
    const msg = input.trim()
    if (!msg || chatMut.isPending) return
    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: msg }])
    chatMut.mutate(msg)
  }

  return (
    <div className="fixed inset-y-0 right-0 w-[420px] bg-background border-l border-border
                    shadow-xl z-50 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <MessageSquare size={14} />
          <span className="text-sm font-semibold">AI 修改助手</span>
          <span className="text-xs text-muted-foreground font-mono">{taskId}</span>
        </div>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.map((m, i) => (
          <div
            key={i}
            className={`text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'bg-foreground text-background rounded-lg px-3 py-2 ml-8'
                : 'text-foreground/80'
            }`}
          >
            {m.content}
          </div>
        ))}
        {chatMut.isPending && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 size={14} className="animate-spin" /> 思考中...
          </div>
        )}
      </div>

      {/* Input */}
      <div className="px-4 py-3 border-t border-border">
        <div className="flex items-center gap-2">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
            placeholder="描述你想修改的内容..."
            disabled={chatMut.isPending}
            className="flex-1 px-3 py-2 text-sm border border-border rounded-md
                       focus:outline-none focus:ring-2 focus:ring-foreground/20
                       disabled:opacity-50"
          />
          <Button
            variant="primary" size="sm"
            disabled={!input.trim() || chatMut.isPending}
            onClick={handleSend}
          >
            <Send size={13} />
          </Button>
        </div>
      </div>
    </div>
  )
}
