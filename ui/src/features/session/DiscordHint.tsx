import { motion } from 'motion/react'
import { cn } from '@/lib/utils'
import { DISCORD } from './copy'
import { enter, rise } from './motion'
import { CAPTION, CARD, HEADING } from './styles'

export function DiscordHint() {
  return (
    <motion.section {...rise} transition={enter()} aria-labelledby="discord-heading" className={cn(CARD, 'px-4 py-3')}>
      <h2 id="discord-heading" className={HEADING}>
        {DISCORD.title}
      </h2>
      <dl className="m-0 mt-1">
        {DISCORD.rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3 border-t-[0.5px] border-card-border py-1.5 first:border-t-0">
            <dt className="text-(--muted-ink)">{row.label}</dt>
            <dd className="m-0 font-semibold">{row.value}</dd>
          </div>
        ))}
      </dl>
      <p className={cn(CAPTION, 'm-0 mt-1 text-pretty')}>{DISCORD.ignore}</p>
    </motion.section>
  )
}
