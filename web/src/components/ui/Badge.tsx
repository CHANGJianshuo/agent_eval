import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/cn'

type Variant = 'default' | 'success' | 'warning' | 'danger' | 'outline'

const variants: Record<Variant, string> = {
  default: 'bg-muted text-muted-foreground border-border',
  success: 'bg-success/10 text-success border-success/20',
  warning: 'bg-warning/10 text-warning border-warning/20',
  danger: 'bg-destructive/10 text-destructive border-destructive/20',
  outline: 'border-border bg-transparent text-foreground/70',
}

interface Props extends HTMLAttributes<HTMLSpanElement> {
  variant?: Variant
}

export function Badge({ variant = 'default', className, ...props }: Props) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md px-2 py-0.5',
        'text-xs font-medium border',
        variants[variant], className,
      )}
      {...props}
    />
  )
}
