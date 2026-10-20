import { motion } from 'motion/react'
import { useId, useRef } from 'react'
import { cn } from '@/lib/utils'
import type { Option } from './catalog'
import { SPRING } from '@/features/session/motion'
import { DISABLED, FOCUS } from './shared'

interface Props {
  options: readonly Option[]
  value: unknown
  onChange: (value: number) => void
  labelledBy?: string
  ariaLabel?: string
  disabled?: boolean
  busy?: boolean
  /** `lg` is the 28px hero control; `quad` also wraps to two rows when the row is narrow. */
  size?: 'md' | 'lg'
  layout?: 'inline' | 'block' | 'quad'
}

/** macOS segmented control: a raised thumb slides between options; arrow keys move the selection. */
export function Segmented({ options, value, onChange, labelledBy, ariaLabel, disabled, busy, size = 'md', layout = 'inline' }: Props) {
  const thumb = useId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const selected = options.findIndex((o) => o.value === value)
  const tabbable = selected >= 0 ? selected : 0

  const move = (from: number, step: number) => {
    const next = (from + step + options.length) % options.length
    onChange(options[next].value)
    refs.current[next]?.focus()
  }

  return (
    <div className={cn(layout === 'quad' && '@container', layout !== 'inline' && 'w-full')}>
      <div
        role="radiogroup"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : ariaLabel}
        aria-busy={busy ? 'true' : undefined}
        aria-disabled={disabled ? 'true' : undefined}
        className={cn(
          'gap-0.5 rounded-[7px] bg-foreground/6 p-0.5',
          layout === 'quad' ? 'grid grid-cols-2 @[34rem]:grid-cols-4' : 'grid auto-cols-fr grid-flow-col',
          layout === 'inline' ? 'inline-grid' : 'w-full',
        )}
      >
        {options.map((option, i) => {
          const on = i === selected
          return (
            <button
              key={option.value}
              ref={(el) => {
                refs.current[i] = el
              }}
              type="button"
              role="radio"
              aria-checked={on}
              tabIndex={i === tabbable ? 0 : -1}
              disabled={disabled}
              onClick={() => !on && onChange(option.value)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') (e.preventDefault(), move(i, 1))
                else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') (e.preventDefault(), move(i, -1))
              }}
              className={cn(
                'relative m-0 rounded-[5px] border-0 bg-transparent px-2.5 text-[12px] leading-tight text-foreground/75',
                'motion-safe:transition-colors motion-safe:duration-150',
                size === 'lg' ? 'min-h-7 py-1' : 'min-h-6 py-0.5',
                !on && !disabled && 'hover:bg-foreground/6 hover:text-foreground active:bg-foreground/12',
                on && 'font-semibold text-foreground',
                FOCUS,
                DISABLED,
              )}
            >
              {on && (
                <motion.span
                  layoutId={thumb}
                  transition={SPRING}
                  aria-hidden="true"
                  className={cn(
                    'absolute inset-0 rounded-[5px] bg-white ring-[0.5px] ring-black/10 dark:bg-[#636366] dark:ring-white/10',
                    'shadow-[0_0.5px_1px_rgb(0_0_0/0.18),0_1px_3px_rgb(0_0_0/0.1)]',
                  )}
                />
              )}
              <span className="relative text-balance">{option.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}
