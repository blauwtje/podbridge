import type { Transition } from 'motion/react'

/* Motion thesis: things settle like native macOS, quick springs you never see
   overshoot; entrances are opacity plus 4px, exits are shorter and ease in. */
export const SPRING = { type: 'spring', stiffness: 400, damping: 32 } as const
export const EASE_OUT = [0.16, 1, 0.3, 1] as const
export const EASE_IN = [0.3, 0, 0.8, 0.15] as const

/** Opacity is tweened so a spring never overshoots it; everything else springs. */
export const enter = (delay = 0): Transition => ({
  default: SPRING,
  opacity: { duration: 0.2, ease: EASE_OUT },
  delay,
})

export const exit: Transition = { duration: 0.12, ease: EASE_IN }

/** Delay between siblings in a stagger, seconds. */
export const STAGGER = 0.04

export const rise = { initial: { opacity: 0, y: 4 }, animate: { opacity: 1, y: 0 } }
