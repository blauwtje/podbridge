import { motion } from 'motion/react'
import type { Notice } from '@/store/snapshot'
import { cn } from '@/lib/utils'
import { noticeTone } from './copy'
import { NoticeIcon } from './icons'
import { enter, rise } from './motion'
import { CAPTION, CARD, HEADING } from './styles'

/**
 * The live region stays in the page while empty so a first notice is announced;
 * with nothing to say it has no box and the card is not drawn.
 */
export function Notices({ notices }: { notices: Notice[] }) {
  // Newest first; the index from the end keeps each row's key stable as notices arrive.
  const rows = notices.map((notice, i) => ({ notice, key: `${i}-${notice.kind}` })).reverse()
  return (
    <section aria-labelledby="notices-heading" aria-live="polite" className="empty:-mt-4">
      {rows.length > 0 && (
        <motion.div {...rise} transition={enter()} className={cn(CARD, 'px-4 py-3')}>
          <h2 id="notices-heading" className={HEADING}>
            Notices
          </h2>
          <ul className="m-0 mt-1 list-none p-0">
            {rows.map(({ notice, key }) => (
              <motion.li
                key={key}
                layout="position"
                {...rise}
                transition={enter()}
                className="flex items-start gap-3 py-1.5"
              >
                <span className="mt-px shrink-0">
                  <NoticeIcon tone={noticeTone(notice.kind)} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-pretty">{notice.message}</span>
                  {notice.next_step && <span className={cn(CAPTION, 'block text-pretty')}>{notice.next_step}</span>}
                </span>
              </motion.li>
            ))}
          </ul>
        </motion.div>
      )}
    </section>
  )
}
