import type { Notice, Step, StepName, StepStatus } from '@/store/snapshot'

export const STEP_ORDER: StepName[] = [
  'checks', 'driver', 'firmware', 'radio', 'pairing', 'connecting', 'connected',
]

const STEP_LABELS: Record<StepName, string> = {
  checks: 'Checks',
  driver: 'Driver',
  firmware: 'Firmware',
  radio: 'Radio',
  pairing: 'Pairing',
  connecting: 'Connecting',
  connected: 'Connected',
}

export const stepLabel = (name: string) => STEP_LABELS[name as StepName] ?? name

export const STATUS_TEXT: Record<StepStatus, string> = {
  pending: 'Pending',
  active: 'In progress',
  done: 'Done',
  failed: 'Failed',
}

/** Notice kinds the run raises: a problem the user may need to act on is a warning. */
const INFO_KINDS = new Set([
  'connect_retry', 'open_case', 'pairing_hint', 'audio_restarting', 'aap_retry', 'radio_reset',
])
export type Tone = 'info' | 'warning'
/** Unknown kinds read as warnings, so a new kind is never understated. */
export const noticeTone = (kind: string): Tone => (INFO_KINDS.has(kind) ? 'info' : 'warning')

const PAIRING_HINT_KINDS = new Set(['pairing_hint', 'open_case'])
/** Notices arrive oldest first; the latest pairing hint wins. */
export function pairingHint(notices: Notice[]): string | undefined {
  return [...notices].reverse().find((n) => PAIRING_HINT_KINDS.has(n.kind))?.message
}

export const isDone = (steps: Step[], name: StepName) =>
  steps.find((s) => s.name === name)?.status === 'done'

/** One line under the title: where the run is. */
export function headline(steps: Step[], failed: boolean): string {
  const bad = steps.find((s) => s.status === 'failed')
  if (bad) return `Stopped at ${stepLabel(bad.name).toLowerCase()}`
  if (failed) return 'Stopped'
  const active = steps.find((s) => s.status === 'active')
  if (active) {
    const attempt = active.attempt ?? 0
    return attempt > 0
      ? `${stepLabel(active.name)}, attempt ${attempt}`
      : `${stepLabel(active.name)} in progress`
  }
  if (isDone(steps, 'connected')) return 'Connected'
  const lastDone = [...steps].reverse().find((s) => s.status === 'done')
  return lastDone ? `${stepLabel(lastDone.name)} done` : 'Waiting to start'
}

export const DISCORD = {
  title: 'In Discord set',
  rows: [
    { label: 'Input', value: 'CABLE Output' },
    { label: 'Output', value: 'Default' },
  ],
  ignore: 'Ignore "CABLE Input" and "CABLE In 16ch": podbridge uses them itself.',
}

export const COPIED_FOR_MS = 2000

const reasonOf = (err: unknown): string => {
  const text =
    err instanceof Error ? err.message : typeof err === 'string' ? err : (err as { message?: unknown } | null)?.message
  return typeof text === 'string' && text.trim() ? text.trim() : 'unknown error'
}

/**
 * Ask the app for the details: it answers with the text, written to the clipboard here.
 * Throws an Error whose message is the user-facing reason.
 */
export async function copyDetails(): Promise<void> {
  const api = window.pywebview?.api
  if (!api) throw new Error('the podbridge app is not connected')
  let text: string
  try {
    text = await api.copy_details()
  } catch (err) {
    throw new Error(reasonOf(err))
  }
  if (!navigator.clipboard) throw new Error('clipboard is not available')
  try {
    await navigator.clipboard.writeText(text)
  } catch (err) {
    throw new Error(reasonOf(err))
  }
}
