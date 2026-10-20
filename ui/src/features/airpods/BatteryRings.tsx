import { animate, motion, useMotionValue, useReducedMotion, useTransform } from 'motion/react'
import { useEffect } from 'react'
import { cn } from '@/lib/utils'
import { EASE_OUT } from '@/features/session/motion'
import { Bolt } from './icons'
import type { Cell, Part } from './types'

const LABEL: Record<Part, string> = { left: 'Left', right: 'Right', case: 'Case' }
const LOW = 20
const RING_SPRING = { type: 'spring', stiffness: 140, damping: 26 } as const

/** Key figure: counts up to its value on first view and on every change; instant under reduced motion. */
function Count({ value, still }: { value: number; still: boolean }) {
  const mv = useMotionValue(still ? value : 0)
  const text = useTransform(mv, (v) => String(Math.round(v)))
  useEffect(() => {
    if (still) {
      mv.set(value)
      return
    }
    const run = animate(mv, value, { duration: 0.7, ease: EASE_OUT })
    return () => run.stop()
  }, [mv, value, still])
  return <motion.span>{text}</motion.span>
}

function describe(part: Part, cell: Cell | null) {
  if (!cell || cell.level === null) return `${LABEL[part]} not reported`
  return `${LABEL[part]} ${cell.level} percent${cell.charging ? ', charging' : ''}${cell.level <= LOW ? ', low' : ''}`
}

function Ring({ part, cell, ear }: { part: Part; cell: Cell | null; ear?: boolean }) {
  const still = useReducedMotion() ?? false
  const level = cell?.level ?? null
  const low = level !== null && level <= LOW
  return (
    <div className="flex flex-col items-center gap-1">
      <div role="img" aria-label={describe(part, cell)} className="relative size-14">
        <svg width="56" height="56" viewBox="0 0 56 56" fill="none" aria-hidden="true" className="absolute inset-0">
          <circle
            cx="28"
            cy="28"
            r="26"
            stroke="currentColor"
            strokeWidth="4"
            strokeDasharray={level === null ? '3 5.2' : undefined}
            className={level === null ? 'text-foreground/25' : 'text-foreground/10'}
          />
          {level !== null && (
            <g transform="rotate(-90 28 28)">
              <motion.circle
                cx="28"
                cy="28"
                r="26"
                stroke="currentColor"
                strokeWidth="4"
                strokeLinecap="round"
                className={low ? 'text-(color:--danger-ink)' : 'text-(color:--success-ink)'}
                initial={{ pathLength: still ? level / 100 : 0, opacity: level > 0 ? 1 : 0 }}
                animate={{ pathLength: level / 100, opacity: level > 0 ? 1 : 0 }}
                transition={still ? { duration: 0 } : { pathLength: RING_SPRING, opacity: { duration: 0.15 } }}
              />
            </g>
          )}
        </svg>
        <span
          aria-hidden="true"
          className="absolute inset-0 grid place-items-center text-[13px] leading-none font-semibold tabular-nums"
        >
          {level === null ? '—' : (
            <span>
              <Count value={level} still={still} />
              <span className="text-[11px] font-medium text-(color:--muted-ink)">%</span>
            </span>
          )}
        </span>
        {cell?.charging && (
          <span
            aria-hidden="true"
            className="absolute -top-0.5 -right-0.5 grid size-[18px] place-items-center rounded-full bg-(--success-ink) text-window ring-2 ring-(--window)"
          >
            <Bolt />
          </span>
        )}
      </div>
      <span className="text-[12px] leading-tight font-medium">{LABEL[part]}</span>
      <span className={cn('min-h-[17px] text-[11px] leading-normal text-(color:--muted-ink)')}>
        {ear === undefined ? '' : ear ? 'In ear' : 'Out of ear'}
      </span>
    </div>
  )
}

export function BatteryRings({
  battery,
  inEar,
}: {
  battery: Record<Part, Cell | null>
  inEar: { left?: boolean; right?: boolean }
}) {
  return (
    <div className="grid grid-cols-3 gap-2 px-3 pt-2 pb-1">
      <Ring part="left" cell={battery.left} ear={inEar.left} />
      <Ring part="right" cell={battery.right} ear={inEar.right} />
      <Ring part="case" cell={battery.case} />
    </div>
  )
}
