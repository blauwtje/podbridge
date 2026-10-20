import type { CSSProperties } from 'react'

/* One raised-surface material for every card: hairline, top-edge highlight,
   two-layer shadow. The error card swaps the shadow for a danger-tinted one. */
export const CARD =
  'rounded-card border-[0.5px] border-card-border bg-card ' +
  'shadow-[inset_0_0.5px_0_rgb(255_255_255/0.5),0_0.5px_1px_rgb(0_0_0/0.06),0_4px_16px_rgb(0_0_0/0.06)]'

export const HEADING = 'text-[13px] font-semibold leading-normal'
export const CAPTION = 'text-[11px] leading-normal text-(--muted-ink)'

/* Status colours darkened toward the foreground so glyphs and small text keep
   contrast on light cards; in dark mode the same mix lightens them. */
export const INK_VARS = {
  '--danger-ink': 'color-mix(in oklab, var(--danger), var(--foreground) 35%)',
  '--success-ink': 'color-mix(in oklab, var(--success), var(--foreground) 25%)',
  '--muted-ink': 'color-mix(in oklab, var(--muted), var(--foreground) 20%)',
  '--warning-ink': 'color-mix(in oklab, var(--warning), var(--foreground) 40%)',
} as CSSProperties
