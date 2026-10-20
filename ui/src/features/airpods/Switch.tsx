import { motion } from 'motion/react'
import { cn } from '@/lib/utils'
import { SPRING } from '@/features/session/motion'
import { DISABLED, FOCUS } from './shared'

interface Props {
  checked: boolean
  onChange: (next: boolean) => void
  labelledBy: string
  disabled?: boolean
  busy?: boolean
}

/** macOS switch: 38x22, the knob springs across. The target is padded to 24px tall without growing the pill. */
export function Switch({ checked, onChange, labelledBy, disabled, busy }: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-busy={busy ? 'true' : undefined}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        'relative m-0 h-[22px] w-[38px] shrink-0 rounded-full border-0 p-0',
        'shadow-[inset_0_0_0_0.5px_rgb(0_0_0/0.12)] after:absolute after:-inset-1 after:content-[""]',
        'motion-safe:transition-colors motion-safe:duration-200',
        checked ? 'bg-accent hover:brightness-105 active:brightness-95' : 'bg-foreground/20 hover:bg-foreground/25 active:bg-foreground/30',
        FOCUS,
        DISABLED,
      )}
    >
      <motion.span
        initial={false}
        animate={{ x: checked ? 16 : 0 }}
        transition={SPRING}
        aria-hidden="true"
        className="absolute top-0.5 left-0.5 size-[18px] rounded-full bg-white shadow-[0_0.5px_1px_rgb(0_0_0/0.25),0_2px_4px_rgb(0_0_0/0.15)]"
      />
    </button>
  )
}
