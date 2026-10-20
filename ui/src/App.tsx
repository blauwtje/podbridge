import { AnimatePresence, MotionConfig, motion } from 'motion/react'
import { useSnapshot } from '@/store/snapshot'
import { DiscordHint } from '@/features/session/DiscordHint'
import { ErrorCard } from '@/features/session/ErrorCard'
import { Notices } from '@/features/session/Notices'
import { StepList } from '@/features/session/StepList'
import { headline, isDone, pairingHint } from '@/features/session/copy'
import { enter, rise } from '@/features/session/motion'
import { INK_VARS } from '@/features/session/styles'

export default function App() {
  const { steps, notices, error } = useSnapshot()
  return (
    <MotionConfig reducedMotion="user">
      <main
        style={INK_VARS}
        className={
          'min-h-dvh bg-[linear-gradient(to_bottom,color-mix(in_oklab,var(--accent)_4%,var(--window)),var(--window)_360px)] ' +
          'text-[13px] leading-normal text-foreground accent-accent caret-accent selection:bg-accent/25 ' +
          'px-4 py-6 sm:px-6'
        }
      >
        <div className="mx-auto flex max-w-[560px] flex-col gap-4">
          <motion.header {...rise} transition={enter()}>
            <h1 className="m-0 text-[22px] leading-tight font-semibold tracking-tight">podbridge</h1>
            <p className="m-0 mt-0.5 text-foreground/70">{headline(steps, error !== null)}</p>
          </motion.header>
          <AnimatePresence>{error && <ErrorCard key="error" error={error} />}</AnimatePresence>
          <StepList steps={steps} pairingHint={pairingHint(notices)} />
          <Notices notices={notices} />
          {isDone(steps, 'connected') && <DiscordHint />}
        </div>
      </main>
    </MotionConfig>
  )
}
