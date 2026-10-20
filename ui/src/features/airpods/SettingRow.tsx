import { AnimatePresence, motion } from 'motion/react'
import { useId, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { StatusIcon } from '@/features/session/icons'
import { enter, exit, rise } from '@/features/session/motion'
import { Fail, Warn } from './icons'
import type { CaptionTone, Setting } from './useSetting'

/** Grouped-inset row: 36px minimum, hairline above every row but the first, inset from the left. */
export const ROW =
  'relative px-3 py-1.5 before:pointer-events-none before:absolute before:inset-x-3 before:top-0 before:h-[0.5px] before:bg-card-border first:before:hidden'

const TONE: Record<CaptionTone, string> = {
  danger: 'text-(color:--danger-ink)',
  warning: 'text-(color:--warning-ink)',
  muted: 'text-(color:--muted-ink)',
}

/** The live region stays in the row while empty so the first message is announced. */
function Caption({ caption }: { caption: Setting['caption'] }) {
  return (
    <div aria-live="polite">
      <AnimatePresence initial={false} mode="popLayout">
        {caption && (
          <motion.p
            key={caption.text}
            {...rise}
            animate={{ ...rise.animate, transition: enter() }}
            exit={{ opacity: 0, transition: exit }}
            className={cn('m-0 flex items-start gap-1 pt-0.5 text-[11px] leading-normal', TONE[caption.tone])}
          >
            {caption.tone !== 'muted' && (
              <span className="mt-[3px] shrink-0">{caption.tone === 'danger' ? <Fail /> : <Warn />}</span>
            )}
            <span className="text-pretty">{caption.text}</span>
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  )
}

interface Props {
  label: string
  ctl: Setting
  /** Control on its own line under the label instead of at the right; a `trailing` readout implies it. */
  block?: boolean
  /** Right-aligned readout beside the label, e.g. a slider value. */
  trailing?: ReactNode
  /** Static note under the control (not live). */
  hint?: ReactNode
  /** False on all but the last row of a pair that shares one setting. */
  showCaption?: boolean
  children: (labelId: string) => ReactNode
}

export function SettingRow({ label, ctl, block, trailing, hint, showCaption = true, children }: Props) {
  const labelId = useId()
  const below = block || trailing !== undefined
  return (
    <div className={ROW}>
      <div className="flex min-h-6 items-center justify-between gap-3">
        <span className="flex min-w-0 items-center gap-2">
          <span id={labelId} className="text-pretty">
            {label}
          </span>
          {ctl.pending && (
            <span aria-hidden="true" data-testid="pending" className="grid shrink-0 [&>svg]:size-3.5">
              <StatusIcon status="active" />
            </span>
          )}
        </span>
        {trailing}
        {!below && children(labelId)}
      </div>
      {below && <div className="pt-1.5">{children(labelId)}</div>}
      {hint && <p className="m-0 pt-1 text-[11px] leading-normal text-(color:--muted-ink)">{hint}</p>}
      {showCaption && <Caption caption={ctl.caption} />}
    </div>
  )
}
