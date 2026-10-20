import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { cn } from '@/lib/utils'
import { FOCUS } from './shared'

interface Props {
  min: number
  max: number
  step: number
  value: unknown
  onCommit: (value: number) => void
  labelledBy?: string
  ariaLabel?: string
  disabled?: boolean
  busy?: boolean
}

const SETTLE_MS = 250

/** Native range input, themed. The display follows the thumb; one write is sent once the user settles. */
export function Slider({ min, max, step, value, onCommit, labelledBy, ariaLabel, disabled, busy }: Props) {
  const [draft, setDraft] = useState<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const current = typeof value === 'number' && Number.isFinite(value) ? value : min
  const shown = draft ?? current

  useEffect(() => () => clearTimeout(timer.current), [])

  const commit = (next: number) => {
    clearTimeout(timer.current)
    setDraft(null)
    if (next !== current) onCommit(next)
  }

  return (
    <input
      type="range"
      min={min}
      max={max}
      step={step}
      value={shown}
      disabled={disabled}
      aria-labelledby={labelledBy}
      aria-label={labelledBy ? undefined : ariaLabel}
      aria-busy={busy ? 'true' : undefined}
      onChange={(e) => {
        const next = Number(e.target.value)
        setDraft(next)
        clearTimeout(timer.current)
        timer.current = setTimeout(() => commit(next), SETTLE_MS)
      }}
      onPointerUp={(e) => commit(Number(e.currentTarget.value))}
      onKeyUp={(e) => commit(Number(e.currentTarget.value))}
      style={{ '--fill': `${((shown - min) / (max - min)) * 100}%` } as CSSProperties}
      className={cn(
        'm-0 block h-6 w-full cursor-pointer appearance-none bg-transparent accent-accent',
        '[&::-webkit-slider-runnable-track]:h-1 [&::-webkit-slider-runnable-track]:rounded-full',
        '[&::-webkit-slider-runnable-track]:bg-[linear-gradient(to_right,var(--accent)_var(--fill),color-mix(in_oklab,var(--foreground)_15%,transparent)_var(--fill))]',
        '[&::-webkit-slider-thumb]:-mt-[7px] [&::-webkit-slider-thumb]:size-[18px] [&::-webkit-slider-thumb]:appearance-none',
        '[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-[0.5px] [&::-webkit-slider-thumb]:border-black/10',
        '[&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:shadow-[0_0.5px_1px_rgb(0_0_0/0.25),0_2px_4px_rgb(0_0_0/0.15)]',
        'active:[&::-webkit-slider-thumb]:bg-[color-mix(in_oklab,white_88%,var(--foreground))]',
        'disabled:cursor-not-allowed disabled:opacity-45',
        FOCUS,
      )}
    />
  )
}
