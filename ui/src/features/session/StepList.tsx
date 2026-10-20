import { AnimatePresence, motion } from 'motion/react'
import type { Step, StepStatus } from '@/store/snapshot'
import { cn } from '@/lib/utils'
import { STATUS_TEXT, STEP_ORDER, stepLabel } from './copy'
import { StatusIcon } from './icons'
import { enter, exit, rise, SPRING, STAGGER } from './motion'
import { CAPTION, CARD } from './styles'

/** Before the first snapshot arrives the list still holds its seven rows, so nothing shifts on load. */
const PLACEHOLDER: Step[] = STEP_ORDER.map((name) => ({ name, status: 'pending' }))

function Glyph({ status }: { status: StepStatus }) {
  return (
    <span className="relative mt-px grid size-[18px] shrink-0 place-items-center" aria-hidden="true">
      <AnimatePresence initial={false}>
        <motion.span
          key={status}
          className="col-start-1 row-start-1 grid place-items-center"
          initial={{ opacity: 0, scale: 0.85 }}
          animate={{ opacity: 1, scale: 1, transition: enter() }}
          exit={{ opacity: 0, scale: 0.85, transition: exit }}
        >
          <StatusIcon status={status} />
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

function Attempt({ n }: { n: number }) {
  return (
    <span className="relative shrink-0 text-[11px] leading-normal text-(--muted-ink) tabular-nums">
      attempt{' '}
      <AnimatePresence initial={false} mode="popLayout">
        <motion.span
          key={n}
          className="inline-block"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0, transition: enter() }}
          exit={{ opacity: 0, transition: exit }}
        >
          {n}
        </motion.span>
      </AnimatePresence>
    </span>
  )
}

export function StepList({ steps, pairingHint }: { steps: Step[]; pairingHint?: string }) {
  const rows = steps.length > 0 ? steps : PLACEHOLDER
  return (
    <ol aria-label="Run steps" className={cn(CARD, 'm-0 list-none p-1')}>
      {rows.map((step, i) => {
        const active = step.status === 'active'
        const attempt = step.attempt ?? 0
        const hint = step.name === 'pairing' && active ? pairingHint : undefined
        return (
          <motion.li
            key={step.name}
            aria-current={active ? 'step' : undefined}
            {...rise}
            transition={enter(i * STAGGER)}
            className={cn(
              'relative flex items-start gap-3 rounded-lg px-3 py-2',
              'after:pointer-events-none after:absolute after:inset-x-3 after:top-0 after:h-px after:bg-card-border',
              'first:after:hidden',
            )}
          >
            {active && (
              <motion.span
                layoutId="active-row"
                transition={SPRING}
                aria-hidden="true"
                className="absolute inset-0 rounded-lg bg-accent/10 ring-[0.5px] ring-accent/20 ring-inset"
              />
            )}
            <span className="relative flex">
              <Glyph status={step.status} />
            </span>
            <span className="relative min-w-0 flex-1">
              <span className={cn('block', active ? 'font-semibold' : 'font-medium', step.status === 'pending' && 'text-(--muted-ink)')}>
                {stepLabel(step.name)}
                <span className="sr-only">: {STATUS_TEXT[step.status]}</span>
              </span>
              {hint && <span className={cn(CAPTION, 'block text-pretty')}>{hint}</span>}
            </span>
            {step.status === 'failed' && (
              <span aria-hidden="true" className="relative shrink-0 text-[11px] leading-normal font-semibold text-(color:--danger-ink)">
                Failed
              </span>
            )}
            {step.name === 'connecting' && attempt > 0 && <Attempt n={attempt} />}
          </motion.li>
        )
      })}
    </ol>
  )
}
