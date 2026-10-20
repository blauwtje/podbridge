import { useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import type { ErrorInfo } from '@/store/snapshot'
import { cn } from '@/lib/utils'
import { COPIED_FOR_MS, copyDetails } from './copy'
import { ErrorIcon } from './icons'
import { enter, exit } from './motion'
import { CAPTION, HEADING } from './styles'

type Feedback = { kind: 'idle' } | { kind: 'copied' } | { kind: 'failed'; reason: string }

export function ErrorCard({ error }: { error: ErrorInfo }) {
  const [feedback, setFeedback] = useState<Feedback>({ kind: 'idle' })
  const [busy, setBusy] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => () => clearTimeout(timer.current), [])

  async function copy() {
    if (busy) return
    clearTimeout(timer.current)
    setFeedback({ kind: 'idle' })
    setBusy(true)
    try {
      await copyDetails()
      setFeedback({ kind: 'copied' })
      timer.current = setTimeout(() => setFeedback({ kind: 'idle' }), COPIED_FOR_MS)
    } catch (err) {
      setFeedback({ kind: 'failed', reason: err instanceof Error ? err.message : 'unknown error' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <motion.div
      role="alert"
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1, transition: enter() }}
      exit={{ opacity: 0, scale: 0.98, transition: exit }}
      className={cn(
        'rounded-card border-[0.5px] border-danger/30 px-4 py-3',
        'bg-[color-mix(in_oklab,var(--danger)_8%,var(--card))]',
        'shadow-[inset_0_0.5px_0_rgb(255_255_255/0.5),0_0.5px_1px_rgb(0_0_0/0.06),0_4px_16px_color-mix(in_oklab,var(--danger)_14%,transparent)]',
      )}
    >
      <div className="flex items-start gap-3">
        <span className="mt-px shrink-0">
          <ErrorIcon />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className={cn(HEADING, 'text-pretty')}>{error.message}</h2>
          {error.next_step && <p className="m-0 mt-0.5 text-pretty">{error.next_step}</p>}
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1">
            <button
              type="button"
              onClick={copy}
              disabled={busy}
              aria-busy={busy}
              className={cn(
                'inline-flex h-7 min-w-24 items-center justify-center rounded-md px-3 text-[13px] font-medium',
                'border-[0.5px] border-black/15 bg-white/90 text-foreground dark:border-white/15 dark:bg-white/12',
                'shadow-[0_0.5px_1px_rgb(0_0_0/0.12)]',
                'hover:bg-white hover:shadow-[0_1px_2px_rgb(0_0_0/0.16)] dark:hover:bg-white/18',
                'active:translate-y-px active:bg-black/5 active:shadow-none dark:active:bg-white/8',
                'disabled:cursor-default disabled:bg-black/5 disabled:text-muted disabled:shadow-none dark:disabled:bg-white/6',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                'motion-safe:transition-[background-color,box-shadow,translate] motion-safe:duration-150 motion-safe:ease-out',
                'pointer-coarse:h-11',
              )}
            >
              {busy ? 'Copying details' : 'Copy details'}
            </button>
            <p role="status" className={cn(CAPTION, 'm-0 min-h-[1.5em] text-pretty')}>
              {feedback.kind === 'copied' && <span className="text-(color:--success-ink)">Copied</span>}
              {feedback.kind === 'failed' && (
                <span className="text-(color:--danger-ink)">Couldn't copy details: {feedback.reason}</span>
              )}
            </p>
          </div>
        </div>
      </div>
    </motion.div>
  )
}
