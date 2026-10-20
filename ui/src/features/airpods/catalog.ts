/* Keys, labels and values mirror CATALOG in src/podbridge/settings.py. */

export interface Def {
  key: string
  label: string
}

export interface Option {
  value: number
  label: string
}

const def = (key: string, label: string): Def => ({ key, label })

export const LISTENING_MODE = def('listening_mode', 'Listening mode')
export const LISTENING_OPTIONS: Option[] = [
  { value: 1, label: 'Off' },
  { value: 2, label: 'Noise Cancellation' },
  { value: 3, label: 'Transparency' },
  { value: 4, label: 'Adaptive' },
]
export const ADAPTIVE_MODE = 4

export const ADAPTIVE_LEVEL = def('adaptive_level', 'Adaptive level')
export const PRESS_HOLD_CYCLE = def('press_hold_cycle', 'Press and hold cycle')
export const PRESS_HOLD_BITS: Option[] = [
  { value: 1, label: 'Off' },
  { value: 2, label: 'Noise Cancellation' },
  { value: 4, label: 'Transparency' },
  { value: 8, label: 'Adaptive' },
]

/** A cycle must keep at least this many modes. */
export const PRESS_BITS_MIN = 2

export const NOISE_SWITCHES: Def[] = [
  def('allow_off', 'Allow Off'),
  def('conversation_awareness', 'Conversation Awareness'),
  def('personalized_volume', 'Personalized Volume'),
  def('one_pod_anc', 'Noise cancellation with one AirPod'),
]

export const PRESS_HOLD_ACTION = def('press_hold_action', 'Press and hold action')
export const PRESS_ACTIONS: Option[] = [
  { value: 1, label: 'Noise control' },
  { value: 5, label: 'Siri' },
]
/** The setting is [right, left]. */
export const PRESS_SIDES = [
  { label: 'Left', index: 1 },
  { label: 'Right', index: 0 },
] as const

const levels = (more: string, most: string): Option[] => [
  { value: 0, label: 'Default' },
  { value: 1, label: more },
  { value: 2, label: most },
]
export const PRESS_SPEED = def('press_speed', 'Press speed')
export const PRESS_SPEED_OPTIONS = levels('Slower', 'Slowest')
export const PRESS_DURATION = def('press_hold_duration', 'Press and hold duration')
export const PRESS_DURATION_OPTIONS = levels('Shorter', 'Shortest')

export const VOLUME_SWIPE = def('volume_swipe', 'Volume swipe')
export const SWIPE_LENGTH = def('swipe_length', 'Swipe length')
export const SWIPE_LENGTH_OPTIONS = levels('Longer', 'Longest')
export const MUTE_END_CALL = def('mute_end_call', 'Mute and end call')
/** The setting is [mute, end]. */
export const CALL_ROWS = [
  {
    label: 'Mute',
    index: 0,
    options: [
      { value: 0x22, label: 'Press twice' },
      { value: 0x23, label: 'Press once' },
    ],
  },
  {
    label: 'End call',
    index: 1,
    options: [
      { value: 0x02, label: 'Press twice' },
      { value: 0x03, label: 'Press once' },
    ],
  },
] as const

export const TONE_VOLUME = def('tone_volume', 'Tone volume')
export const TONE_RANGE = { min: 15, max: 100 } as const
export const MICROPHONE = def('microphone', 'Microphone')
export const MICROPHONE_OPTIONS: Option[] = [
  { value: 0, label: 'Automatic' },
  { value: 1, label: 'Always right' },
  { value: 2, label: 'Always left' },
]
export const SOUND_SWITCHES: Def[] = [
  def('ear_detection', 'Automatic ear detection'),
  def('sleep_pause', 'Pause media when falling asleep'),
  def('case_sounds', 'Charging case sounds'),
]

export const HEART_RATE = def('heart_rate', 'Heart rate')
export const HEARING_ASSIST = def('hearing_assist', 'Hearing Assist')

export const APPLE_ONLY = ['Firmware update', 'Find My', 'Hearing test', 'Ear tip fit test'] as const

/** A switch writes 1 for on and 2 for off. */
export const SWITCH_ON = 1
export const SWITCH_OFF = 2
