import type { StepStatus } from '@/store/snapshot'
import type { Tone } from './copy'

/* 18px glyph box; strokes follow currentColor so the tone comes from the caller. */
const BOX = { width: 18, height: 18, viewBox: '0 0 18 18', fill: 'none', 'aria-hidden': true } as const

export function StatusIcon({ status }: { status: StepStatus }) {
  if (status === 'active')
    return (
      <svg {...BOX} className="text-accent motion-safe:animate-spin">
        <circle cx="9" cy="9" r="7" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2" />
        <path d="M9 2a7 7 0 0 1 7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    )
  if (status === 'done')
    return (
      <svg {...BOX} className="text-(color:--success-ink)">
        <circle cx="9" cy="9" r="8" fill="currentColor" />
        <path d="m5.5 9.2 2.4 2.4 4.6-5" className="stroke-window" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    )
  if (status === 'failed')
    return (
      <svg {...BOX} className="text-(color:--danger-ink)">
        <circle cx="9" cy="9" r="8" fill="currentColor" />
        <path d="m6 6 6 6m0-6-6 6" className="stroke-window" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    )
  return (
    <svg {...BOX} className="text-(--muted-ink)">
      <circle cx="9" cy="9" r="7" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.7" />
    </svg>
  )
}

export function NoticeIcon({ tone }: { tone: Tone }) {
  if (tone === 'info')
    return (
      <svg {...BOX} className="text-accent">
        <circle cx="9" cy="9" r="7.25" stroke="currentColor" strokeWidth="1.5" />
        <path d="M9 8.2v4M9 5.6v.1" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    )
  return (
    <svg {...BOX} className="text-(color:--warning-ink)">
      <path d="M9 2.4 16.2 15H1.8L9 2.4Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M9 7v3.4M9 12.6v.1" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  )
}

export function ErrorIcon() {
  return (
    <svg {...BOX} className="text-(color:--danger-ink)">
      <circle cx="9" cy="9" r="8" fill="currentColor" />
      <path d="M9 5v5M9 12.6v.1" className="stroke-window" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  )
}
