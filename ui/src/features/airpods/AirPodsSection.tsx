import { motion } from 'motion/react'
import { useMemo, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { enter, rise, STAGGER } from '@/features/session/motion'
import { CAPTION, CARD, HEADING } from '@/features/session/styles'
import { BatteryRings } from './BatteryRings'
import {
  APPLE_ONLY,
  CALL_ROWS,
  HEARING_ASSIST,
  HEART_RATE,
  LISTENING_MODE,
  LISTENING_OPTIONS,
  MICROPHONE,
  MICROPHONE_OPTIONS,
  MUTE_END_CALL,
  NOISE_SWITCHES,
  PRESS_ACTIONS,
  PRESS_DURATION,
  PRESS_DURATION_OPTIONS,
  PRESS_HOLD_ACTION,
  PRESS_HOLD_CYCLE,
  PRESS_SIDES,
  PRESS_SPEED,
  PRESS_SPEED_OPTIONS,
  SOUND_SWITCHES,
  SWIPE_LENGTH,
  SWIPE_LENGTH_OPTIONS,
  TONE_RANGE,
  TONE_VOLUME,
  VOLUME_SWIPE,
} from './catalog'
import { AdaptiveRow, BitmaskRow, ChoiceRow, PairRows, SliderRow, SwitchRow, type Settings } from './rows'
import { ROW } from './SettingRow'
import { readAirPods, type AirPodsView } from './types'
import { useSetting } from './useSetting'

function Card({ id, title, index, children }: { id: string; title: string; index: number; children: ReactNode }) {
  return (
    <motion.section aria-labelledby={id} {...rise} transition={enter(index * STAGGER)} className={cn(CARD, 'py-2')}>
      <h3 id={id} className={cn(HEADING, 'm-0 px-3 pb-1')}>
        {title}
      </h3>
      <div>{children}</div>
    </motion.section>
  )
}

function Battery({ view }: { view: AirPodsView }) {
  return (
    <Card id="ap-battery" title={view.name ?? 'AirPods'} index={0}>
      <BatteryRings battery={view.battery} inEar={view.inEar} />
    </Card>
  )
}

function NoiseControl({ settings }: { settings: Settings }) {
  const mode = useSetting(LISTENING_MODE, settings)
  const cycle = PRESS_HOLD_CYCLE
  return (
    <Card id="ap-noise" title="Noise Control" index={1}>
      <ChoiceRow def={LISTENING_MODE} ctl={mode} options={LISTENING_OPTIONS} size="lg" layout="quad" />
      <AdaptiveRow settings={settings} mode={mode.value} />
      <BitmaskRow def={cycle} settings={settings} />
      {NOISE_SWITCHES.map((def) => (
        <SwitchRow key={def.key} def={def} settings={settings} />
      ))}
    </Card>
  )
}

function PressHold({ settings }: { settings: Settings }) {
  const speed = useSetting(PRESS_SPEED, settings)
  const duration = useSetting(PRESS_DURATION, settings)
  return (
    <Card id="ap-press" title="Press and Hold" index={2}>
      <PairRows def={PRESS_HOLD_ACTION} settings={settings} rows={PRESS_SIDES.map((s) => ({ ...s, options: PRESS_ACTIONS }))} />
      <ChoiceRow def={PRESS_SPEED} ctl={speed} options={PRESS_SPEED_OPTIONS} />
      <ChoiceRow def={PRESS_DURATION} ctl={duration} options={PRESS_DURATION_OPTIONS} />
    </Card>
  )
}

function Controls({ settings }: { settings: Settings }) {
  const length = useSetting(SWIPE_LENGTH, settings)
  return (
    <Card id="ap-controls" title="Controls" index={3}>
      <SwitchRow def={VOLUME_SWIPE} settings={settings} />
      <ChoiceRow def={SWIPE_LENGTH} ctl={length} options={SWIPE_LENGTH_OPTIONS} />
      <PairRows def={MUTE_END_CALL} settings={settings} rows={CALL_ROWS} />
    </Card>
  )
}

function Sound({ settings }: { settings: Settings }) {
  const tone = useSetting(TONE_VOLUME, settings)
  const mic = useSetting(MICROPHONE, settings)
  return (
    <Card id="ap-sound" title="Sound" index={4}>
      <SliderRow def={TONE_VOLUME} ctl={tone} min={TONE_RANGE.min} max={TONE_RANGE.max} step={1} readout />
      <ChoiceRow def={MICROPHONE} ctl={mic} options={MICROPHONE_OPTIONS} layout="block" />
      {SOUND_SWITCHES.map((def) => (
        <SwitchRow key={def.key} def={def} settings={settings} />
      ))}
    </Card>
  )
}

function Health({ settings, enrolled }: { settings: Settings; enrolled: boolean }) {
  return (
    <Card id="ap-health" title="Health" index={5}>
      <SwitchRow def={HEART_RATE} settings={settings} />
      {enrolled && <SwitchRow def={HEARING_ASSIST} settings={settings} />}
    </Card>
  )
}

function About({ view }: { view: AirPodsView }) {
  const rows: [string, string | undefined][] = [
    ['Name', view.name],
    ['Model', view.model],
    ['Serial number', view.serial],
    ['Firmware', view.firmware],
  ]
  return (
    <Card id="ap-about" title="About" index={6}>
      <dl className="m-0">
        {rows.map(([label, value]) => (
          <div key={label} className={cn(ROW, 'flex min-h-9 items-center justify-between gap-3')}>
            <dt>{label}</dt>
            <dd className="m-0 min-w-0 text-right break-words text-(color:--muted-ink) tabular-nums select-text">{value ?? '—'}</dd>
          </div>
        ))}
      </dl>
    </Card>
  )
}

/** Features that live in Apple's own software: shown so the gap is explained, with nothing to press. */
function AppleOnly() {
  return (
    <Card id="ap-apple" title="On iPhone or Mac" index={7}>
      <ul className="m-0 list-none p-0">
        {APPLE_ONLY.map((label) => (
          <li key={label} aria-disabled="true" className={ROW}>
            <span className="block opacity-55">{label}</span>
            <span className={cn(CAPTION, 'block')}>Needs an iPhone or Mac</span>
          </li>
        ))}
      </ul>
    </Card>
  )
}

export function AirPodsSection({ airpods }: { airpods: Record<string, unknown> }) {
  const view = useMemo(() => readAirPods(airpods), [airpods])
  return (
    <section aria-labelledby="airpods-heading" className="flex flex-col gap-2">
      <h2 id="airpods-heading" className="m-0 text-[15px] leading-snug font-semibold">
        AirPods
      </h2>
      {view.empty ? (
        <motion.div {...rise} transition={enter()} className={cn(CARD, 'px-4 py-3')}>
          <p className={cn(CAPTION, 'm-0')}>AirPods controls appear once your AirPods connect.</p>
        </motion.div>
      ) : (
        <div className="flex flex-col gap-4">
          <Battery view={view} />
          <NoiseControl settings={view.settings} />
          <PressHold settings={view.settings} />
          <Controls settings={view.settings} />
          <Sound settings={view.settings} />
          <Health settings={view.settings} enrolled={view.enrolled} />
          <About view={view} />
          <AppleOnly />
        </div>
      )}
    </section>
  )
}
