import { useSyncExternalStore } from 'react'

export type StepName =
  | 'checks' | 'driver' | 'firmware' | 'radio' | 'pairing' | 'connecting' | 'connected'
export type StepStatus = 'pending' | 'active' | 'done' | 'failed'

export interface Step {
  name: StepName
  status: StepStatus
  /** connect attempt count, set on `connecting` */
  attempt?: number
}

export interface Notice {
  kind: string
  message: string
  next_step?: string
}

export interface ErrorInfo {
  message: string
  next_step: string
  details: string
}

export type SettingStatus = 'confirmed' | 'pending' | 'unsupported'

export interface SettingValue {
  value: unknown
  status: SettingStatus
}

export interface Snapshot {
  steps: Step[]
  notices: Notice[]
  error: ErrorInfo | null
  airpods: Record<string, unknown>
}

export interface PbApi {
  get_snapshot(): Promise<Snapshot>
  set_setting(key: string, value: unknown): Promise<{ ok: boolean; error?: string | null }>
  copy_details(): Promise<{ ok: boolean; error?: string | null }>
}

declare global {
  interface Window {
    __pb?: { emit: (snapshot: Snapshot) => void }
    pywebview?: { api: PbApi }
  }
}

export const EMPTY: Snapshot = { steps: [], notices: [], error: null, airpods: {} }

let current: Snapshot = EMPTY
const listeners = new Set<() => void>()

export function setSnapshot(next: Snapshot) {
  current = next
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** Install window.__pb.emit and load the first snapshot once pywebview is ready. */
export function connectBridge() {
  window.__pb = { emit: setSnapshot }
  const load = () => window.pywebview?.api.get_snapshot().then(setSnapshot).catch(() => {})
  if (window.pywebview) load()
  else window.addEventListener('pywebviewready', load, { once: true })
}

export function useSnapshot(): Snapshot {
  return useSyncExternalStore(subscribe, () => current)
}
