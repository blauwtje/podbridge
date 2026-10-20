import type { SettingStatus, SettingValue } from '@/store/snapshot'

/** One battery component. `level` is null when the AirPods did not report it. */
export interface Cell {
  level: number | null
  charging: boolean
}

export type Part = 'left' | 'right' | 'case'

export interface AirPodsView {
  /** True when the snapshot carries no AirPods data at all. */
  empty: boolean
  name?: string
  model?: string
  serial?: string
  firmware?: string
  battery: Record<Part, Cell | null>
  inEar: { left?: boolean; right?: boolean }
  enrolled: boolean
  settings: Record<string, SettingValue>
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const text = (v: unknown) => (typeof v === 'string' && v.trim() !== '' ? v : undefined)
const STATUSES: readonly SettingStatus[] = ['confirmed', 'pending', 'unsupported']

function cell(v: unknown): Cell | null {
  if (!isRecord(v)) return null
  const level = typeof v.level === 'number' && Number.isFinite(v.level) ? Math.min(100, Math.max(0, Math.round(v.level))) : null
  return { level, charging: v.charging === true }
}

/** Reads `Snapshot.airpods` without trusting it: a missing or odd field becomes "not reported", never a throw. */
export function readAirPods(raw: Record<string, unknown> | null | undefined): AirPodsView {
  const src = isRecord(raw) ? raw : {}
  const battery = isRecord(src.battery) ? src.battery : {}
  const ear = isRecord(src.in_ear) ? src.in_ear : {}
  const settings: Record<string, SettingValue> = {}
  if (isRecord(src.settings)) {
    for (const [key, entry] of Object.entries(src.settings)) {
      // Keep the snapshot's own object so its identity changes only when a new snapshot arrives.
      if (isRecord(entry) && STATUSES.includes(entry.status as SettingStatus)) settings[key] = entry as unknown as SettingValue
    }
  }
  return {
    empty: Object.keys(src).length === 0,
    name: text(src.name),
    model: text(src.model),
    serial: text(src.serial),
    firmware: text(src.firmware),
    battery: { left: cell(battery.left), right: cell(battery.right), case: cell(battery.case) },
    inEar: {
      left: typeof ear.left === 'boolean' ? ear.left : undefined,
      right: typeof ear.right === 'boolean' ? ear.right : undefined,
    },
    enrolled: src.enrolled === true,
    settings,
  }
}
