import { useCallback, useEffect, useRef, useState } from 'react'
import type { SettingValue } from '@/store/snapshot'
import type { Def } from './catalog'

export type CaptionTone = 'danger' | 'warning' | 'muted'

export interface Setting {
  def: Def
  /** The value to show: the user's unconfirmed change if there is one, else the snapshot's. */
  value: unknown
  /** No snapshot entry yet: the control is disabled. */
  waiting: boolean
  pending: boolean
  caption: { tone: CaptionTone; text: string } | null
  set: (value: unknown) => void
}

/**
 * One setting's write behaviour: optimistic value while a write is pending, the
 * snapshot's word once it arrives, and a caption for each way a write can fail.
 */
export function useSetting(def: Def, settings: Record<string, SettingValue>): Setting {
  const entry: SettingValue | undefined = settings[def.key]
  const [local, setLocal] = useState<{ value: unknown } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const latest = useRef(0)

  // A new snapshot entry settles the write: confirmed or unsupported both hand the value back to the snapshot.
  useEffect(() => {
    setDismissed(false)
    if (entry && entry.status !== 'pending') setLocal(null)
  }, [entry])

  const set = useCallback(
    (value: unknown) => {
      const mine = ++latest.current
      setLocal({ value })
      setFailure(null)
      setDismissed(true)
      const fail = (reason: string) => {
        if (latest.current !== mine) return
        setLocal(null)
        setFailure(`Couldn't change ${def.label}: ${reason}`)
      }
      const api = window.pywebview?.api
      if (!api) return fail('no connection to podbridge')
      try {
        api.set_setting(def.key, value).then(
          (res) => {
            if (!res || res.ok !== true) fail(res?.error || 'podbridge did not accept it')
          },
          (err: unknown) => fail(err instanceof Error && err.message ? err.message : 'the request failed'),
        )
      } catch (err) {
        fail(err instanceof Error && err.message ? err.message : 'the request failed')
      }
    },
    [def.key, def.label],
  )

  const waiting = entry === undefined
  const caption: Setting['caption'] = failure
    ? { tone: 'danger', text: failure }
    : entry?.status === 'unsupported' && !dismissed
      ? { tone: 'warning', text: 'Not supported on these AirPods' }
      : waiting
        ? { tone: 'muted', text: 'Waiting for the AirPods' }
        : null

  return {
    def,
    value: local ? local.value : entry?.value,
    waiting,
    pending: local !== null || entry?.status === 'pending',
    caption,
    set,
  }
}
