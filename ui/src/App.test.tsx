import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import App from './App'
import { EMPTY, setSnapshot, type Notice, type Snapshot, type Step } from './store/snapshot'

const NAMES = ['checks', 'driver', 'firmware', 'radio', 'pairing', 'connecting', 'connected'] as const

function steps(upTo: (typeof NAMES)[number] | null, status: Step['status'], extra: Partial<Step> = {}): Step[] {
  const at = upTo ? NAMES.indexOf(upTo) : -1
  return NAMES.map((name, i) => {
    if (i < at) return { name, status: 'done' }
    if (i === at) return { name, status, ...extra }
    return { name, status: 'pending' }
  })
}

function show(partial: Partial<Snapshot>) {
  act(() => setSnapshot({ ...EMPTY, ...partial }))
}

const row = (label: string) => screen.getByText(label).closest('li')!

const stubClipboard = (writeText: (text: string) => Promise<void>) =>
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })

beforeEach(() => {
  stubClipboard(vi.fn().mockResolvedValue(undefined))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  delete window.pywebview
  act(() => setSnapshot(EMPTY))
})

test('holds all seven steps as pending before the first snapshot', () => {
  render(<App />)
  const items = within(screen.getByRole('list', { name: 'Run steps' })).getAllByRole('listitem')
  expect(items).toHaveLength(7)
  expect(screen.getByText('Waiting to start')).toBeTruthy()
})

test('marks the active step and shows the connect attempt', () => {
  render(<App />)
  show({ steps: steps('connecting', 'active', { attempt: 3 }) })
  expect(row('Connecting').getAttribute('aria-current')).toBe('step')
  expect(row('Connecting').textContent).toContain('attempt 3')
  expect(row('Connecting').textContent).toContain('In progress')
  expect(row('Radio').getAttribute('aria-current')).toBeNull()
  expect(row('Radio').textContent).toContain('Done')
  expect(screen.getByText('Connecting, attempt 3')).toBeTruthy()
})

test('shows no attempt count until a connect attempt has been made', () => {
  render(<App />)
  show({ steps: steps('connecting', 'active', { attempt: 0 }) })
  expect(row('Connecting').textContent).not.toContain('attempt')
})

test('shows the latest pairing hint under the pairing step only while it is active', () => {
  render(<App />)
  const notices: Notice[] = [
    { kind: 'pairing_hint', message: 'Hold the case button for 3 seconds.' },
    { kind: 'open_case', message: 'Open the case lid next to the PC.' },
  ]
  show({ steps: steps('pairing', 'active'), notices })
  expect(row('Pairing').textContent).toContain('Open the case lid next to the PC.')
  expect(row('Pairing').textContent).not.toContain('Hold the case button')

  show({ steps: steps('connecting', 'active', { attempt: 1 }), notices })
  expect(row('Pairing').textContent).not.toContain('Open the case lid')
})

test('shows the Discord hint only once connected is done', () => {
  render(<App />)
  show({ steps: steps('connecting', 'active', { attempt: 1 }) })
  expect(screen.queryByText('In Discord set')).toBeNull()

  show({ steps: steps('connected', 'done') })
  expect(screen.getByText('In Discord set')).toBeTruthy()
  expect(screen.getByText('CABLE Output')).toBeTruthy()
  expect(screen.getByText('Default')).toBeTruthy()
  expect(screen.getByText(/Ignore "CABLE Input" and "CABLE In 16ch"/)).toBeTruthy()
  expect(screen.getByText('Connected', { selector: 'p' })).toBeTruthy()
})

test('lists notices newest first in a polite live region, unknown kinds included', () => {
  render(<App />)
  const live = document.querySelector<HTMLElement>('[aria-live="polite"]')!
  expect(live).toBeTruthy()
  expect(live.childElementCount).toBe(0)

  show({
    notices: [
      { kind: 'connect_retry', message: 'Retrying connect, attempt 2' },
      { kind: 'mic_silent', message: 'The microphone is silent.', next_step: 'Check the mute switch.' },
      { kind: 'something_new', message: 'A kind the UI has never heard of' },
    ],
  })
  expect(screen.getByRole('region', { name: 'Notices' })).toBe(live)
  const items = within(live).getAllByRole('listitem')
  expect(items.map((li) => li.textContent)).toEqual([
    'A kind the UI has never heard of',
    'The microphone is silent.Check the mute switch.',
    'Retrying connect, attempt 2',
  ])
})

test('error card names the problem and the next step', () => {
  render(<App />)
  show({
    steps: steps('pairing', 'failed'),
    error: { message: 'Pairing timed out', next_step: 'Put the AirPods in the case and retry.', details: 'pairing_timeout' },
  })
  const alert = screen.getByRole('alert')
  expect(within(alert).getByRole('heading').textContent).toBe('Pairing timed out')
  expect(alert.textContent).toContain('Put the AirPods in the case and retry.')
  expect(row('Pairing').textContent).toContain('Failed')
  expect(screen.getByText('Stopped at pairing')).toBeTruthy()
})

const failure = { message: 'Radio did not answer', next_step: 'Unplug and replug the dongle.', details: 'radio_timeout' }

test('Copy details writes what the app returns to the clipboard and says so for 2 seconds', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const writeText = vi.fn().mockResolvedValue(undefined)
  stubClipboard(writeText)
  const copy_details = vi.fn().mockResolvedValue('Radio did not answer\nradio_timeout')
  window.pywebview = { api: { copy_details } as never }
  render(<App />)
  show({ error: failure })

  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copy details' })))
  expect(copy_details).toHaveBeenCalledTimes(1)
  expect(writeText).toHaveBeenCalledWith('Radio did not answer\nradio_timeout')
  expect(screen.getByRole('status').textContent).toBe('Copied')

  act(() => void vi.advanceTimersByTime(1900))
  expect(screen.getByRole('status').textContent).toBe('Copied')
  act(() => void vi.advanceTimersByTime(200))
  expect(screen.getByRole('status').textContent).toBe('')
})

test.each([
  ['the call rejects', () => Promise.reject(new Error('bridge went away')), 'bridge went away'],
  ['the clipboard write fails', () => Promise.resolve('text'), 'permission denied'],
])('Copy details keeps the error card and says why when %s', async (_name, call, reason) => {
  stubClipboard(vi.fn().mockRejectedValue(new Error('permission denied')))
  window.pywebview = { api: { copy_details: vi.fn(call) } as never }
  render(<App />)
  show({ steps: steps('radio', 'failed'), error: failure })

  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copy details' })))
  expect(screen.getByText(`Couldn't copy details: ${reason}`)).toBeTruthy()
  const alert = screen.getByRole('alert')
  expect(within(alert).getByRole('heading').textContent).toBe(failure.message)
  expect(alert.textContent).toContain(failure.next_step)
  expect((screen.getByRole('button', { name: 'Copy details' }) as HTMLButtonElement).disabled).toBe(false)
})

test('Copy details explains itself when there is no app bridge', async () => {
  render(<App />)
  show({ error: failure })
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Copy details' })))
  expect(screen.getByText(/Couldn't copy details: .+/)).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain(failure.message)
})

type Bag = Record<string, unknown>

const pods = (over: Bag = {}, settings: Bag = {}) => ({
  name: 'Thomas AirPods',
  model: 'AirPods Pro 2',
  serial: 'F9XYZ123',
  firmware: '7A305',
  battery: { left: { level: 80, charging: true }, right: { level: 15, charging: false } },
  in_ear: { left: true, right: false },
  settings: {
    listening_mode: { value: 2, status: 'confirmed' },
    allow_off: { value: 1, status: 'confirmed' },
    tone_volume: { value: 50, status: 'confirmed' },
    ...settings,
  },
  ...over,
})

const bridge = (set_setting = vi.fn().mockResolvedValue({ ok: true })) => {
  window.pywebview = { api: { set_setting } as never }
  return set_setting
}
const modeGroup = () => screen.getByRole('radiogroup', { name: 'Listening mode' })
const radio = (name: string) => within(modeGroup()).getByRole('radio', { name })

test('shows the empty state and no controls until AirPods data arrives', () => {
  render(<App />)
  show({ airpods: {} })
  expect(screen.getByText('AirPods controls appear once your AirPods connect.')).toBeTruthy()
  expect(screen.queryByRole('radiogroup')).toBeNull()
  expect(screen.queryByRole('switch')).toBeNull()
})

test('draws one labelled battery ring per component, with charging, low and not-reported states', () => {
  render(<App />)
  show({ airpods: pods() })
  expect(screen.getByRole('img', { name: 'Left 80 percent, charging' })).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Right 15 percent, low' })).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Case not reported' })).toBeTruthy()
  expect(screen.getByText('In ear')).toBeTruthy()
  expect(screen.getByText('Out of ear')).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Thomas AirPods' })).toBeTruthy()
})

test('reads odd snapshot data without throwing', () => {
  render(<App />)
  show({ airpods: { battery: { left: 'full', right: { level: 'high' } }, settings: { listening_mode: 7, allow_off: { value: 1, status: 'bogus' } } } })
  expect(screen.getByRole('img', { name: 'Left not reported' })).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Right not reported' })).toBeTruthy()
  expect(within(modeGroup()).getAllByRole('radio').every((r) => r.getAttribute('aria-checked') === 'false')).toBe(true)
})

test('listening mode is a radiogroup of four with the snapshot value checked', () => {
  render(<App />)
  show({ airpods: pods() })
  const radios = within(modeGroup()).getAllByRole('radio')
  expect(radios.map((r) => r.textContent)).toEqual(['Off', 'Noise Cancellation', 'Transparency', 'Adaptive'])
  expect(radio('Noise Cancellation').getAttribute('aria-checked')).toBe('true')
  expect(radio('Transparency').getAttribute('aria-checked')).toBe('false')
})

test('choosing Transparency writes 3, shows it at once and marks the row busy until confirmed', async () => {
  const set_setting = bridge()
  render(<App />)
  show({ airpods: pods() })
  expect(screen.queryAllByTestId('pending')).toHaveLength(0)

  await act(async () => fireEvent.click(radio('Transparency')))
  expect(set_setting).toHaveBeenCalledWith('listening_mode', 3)
  expect(radio('Transparency').getAttribute('aria-checked')).toBe('true')
  expect(modeGroup().getAttribute('aria-busy')).toBe('true')
  expect(screen.getAllByTestId('pending')).toHaveLength(1)

  show({ airpods: pods({}, { listening_mode: { value: 3, status: 'confirmed' } }) })
  expect(screen.queryAllByTestId('pending')).toHaveLength(0)
  expect(modeGroup().getAttribute('aria-busy')).toBeNull()
  expect(radio('Transparency').getAttribute('aria-checked')).toBe('true')
})

test('arrow keys move the listening mode selection', async () => {
  const set_setting = bridge()
  render(<App />)
  show({ airpods: pods() })
  await act(async () => fireEvent.keyDown(radio('Noise Cancellation'), { key: 'ArrowRight' }))
  expect(set_setting).toHaveBeenCalledWith('listening_mode', 3)
  expect(radio('Noise Cancellation').getAttribute('tabindex')).toBe('-1')
  expect(radio('Transparency').getAttribute('tabindex')).toBe('0')
})

test('an unsupported setting keeps the last confirmed value and says so', () => {
  render(<App />)
  show({ airpods: pods({}, { listening_mode: { value: 2, status: 'unsupported' } }) })
  expect(screen.getByText('Not supported on these AirPods')).toBeTruthy()
  expect(radio('Noise Cancellation').getAttribute('aria-checked')).toBe('true')
})

test('a rejected write reverts to the snapshot value and says why', async () => {
  bridge(vi.fn().mockRejectedValue(new Error('link lost')))
  render(<App />)
  show({ airpods: pods() })
  await act(async () => fireEvent.click(radio('Transparency')))
  expect(screen.getByText("Couldn't change Listening mode: link lost")).toBeTruthy()
  expect(radio('Noise Cancellation').getAttribute('aria-checked')).toBe('true')
  expect(radio('Transparency').getAttribute('aria-checked')).toBe('false')
  expect(screen.queryAllByTestId('pending')).toHaveLength(0)
})

test('an ok: false answer and a missing bridge are reported the same way', async () => {
  bridge(vi.fn().mockResolvedValue({ ok: false, error: 'AirPods out of range' }))
  render(<App />)
  show({ airpods: pods() })
  await act(async () => fireEvent.click(radio('Off')))
  expect(screen.getByText("Couldn't change Listening mode: AirPods out of range")).toBeTruthy()

  delete window.pywebview
  await act(async () => fireEvent.click(radio('Adaptive')))
  expect(screen.getByText("Couldn't change Listening mode: no connection to podbridge")).toBeTruthy()
  expect(radio('Noise Cancellation').getAttribute('aria-checked')).toBe('true')
})

test('a switch writes 2 when turned off from 1', async () => {
  const set_setting = bridge()
  render(<App />)
  show({ airpods: pods() })
  const toggle = screen.getByRole('switch', { name: 'Allow Off' })
  expect(toggle.getAttribute('aria-checked')).toBe('true')
  await act(async () => fireEvent.click(toggle))
  expect(set_setting).toHaveBeenCalledWith('allow_off', 2)
  expect(toggle.getAttribute('aria-checked')).toBe('false')
  expect(toggle.getAttribute('aria-busy')).toBe('true')
})

test('settings the AirPods have not reported yet are disabled and say they are waiting', () => {
  render(<App />)
  show({ airpods: pods() })
  const toggle = screen.getByRole('switch', { name: 'Personalized Volume' }) as HTMLButtonElement
  expect(toggle.disabled).toBe(true)
  expect(screen.getAllByText('Waiting for the AirPods').length).toBeGreaterThan(1)
})

test('the tone volume slider sends one value once the user settles', async () => {
  const set_setting = bridge()
  render(<App />)
  show({ airpods: pods() })
  const slider = screen.getByRole('slider', { name: 'Tone volume' }) as HTMLInputElement
  expect(slider.min).toBe('15')
  expect(slider.max).toBe('100')
  fireEvent.change(slider, { target: { value: '62' } })
  fireEvent.change(slider, { target: { value: '64' } })
  expect(set_setting).not.toHaveBeenCalled()
  await act(async () => fireEvent.keyUp(slider))
  expect(set_setting).toHaveBeenCalledTimes(1)
  expect(set_setting).toHaveBeenCalledWith('tone_volume', 64)
})

test('the adaptive level slider is only enabled in Adaptive mode', () => {
  render(<App />)
  show({ airpods: pods({}, { adaptive_level: { value: 50, status: 'confirmed' } }) })
  expect((screen.getByRole('slider', { name: 'Adaptive level' }) as HTMLInputElement).disabled).toBe(true)
  expect(screen.getByText('Used in Adaptive mode')).toBeTruthy()

  show({ airpods: pods({}, { adaptive_level: { value: 50, status: 'confirmed' }, listening_mode: { value: 4, status: 'confirmed' } }) })
  expect((screen.getByRole('slider', { name: 'Adaptive level' }) as HTMLInputElement).disabled).toBe(false)
})

test('the press and hold cycle keeps at least two modes', async () => {
  const set_setting = bridge()
  render(<App />)
  show({ airpods: pods({}, { press_hold_cycle: { value: 3, status: 'confirmed' } }) })
  const off = screen.getByRole('checkbox', { name: 'Off' })
  expect(off.getAttribute('aria-disabled')).toBe('true')
  expect(screen.getByText('Choose at least two')).toBeTruthy()
  await act(async () => fireEvent.click(off))
  expect(set_setting).not.toHaveBeenCalled()
  await act(async () => fireEvent.click(screen.getByRole('checkbox', { name: 'Transparency' })))
  expect(set_setting).toHaveBeenCalledWith('press_hold_cycle', 7)
})

test('a pair setting rewrites only the element that changed', async () => {
  const set_setting = bridge()
  render(<App />)
  show({ airpods: pods({}, { press_hold_action: { value: [1, 5], status: 'confirmed' } }) })
  const left = screen.getByRole('radiogroup', { name: 'Press and hold action: Left' })
  expect(within(left).getByRole('radio', { name: 'Siri' }).getAttribute('aria-checked')).toBe('true')
  await act(async () => fireEvent.click(within(left).getByRole('radio', { name: 'Noise control' })))
  expect(set_setting).toHaveBeenCalledWith('press_hold_action', [1, 1])
})

test('Apple-only features are greyed and say what they need', () => {
  render(<App />)
  show({ airpods: pods() })
  const notes = screen.getAllByText('Needs an iPhone or Mac')
  expect(notes).toHaveLength(4)
  for (const label of ['Firmware update', 'Find My', 'Hearing test', 'Ear tip fit test'])
    expect(screen.getByText(label).closest('li')!.getAttribute('aria-disabled')).toBe('true')
})

test('Hearing Assist appears only for enrolled users', () => {
  render(<App />)
  show({ airpods: pods() })
  expect(screen.queryByRole('switch', { name: 'Hearing Assist' })).toBeNull()
  show({ airpods: pods({ enrolled: true }) })
  expect(screen.getByRole('switch', { name: 'Hearing Assist' })).toBeTruthy()
})

test('About shows the device details and a dash for what is missing', () => {
  render(<App />)
  show({ airpods: pods({ serial: undefined }) })
  const about = screen.getByRole('region', { name: 'About' })
  expect(about.textContent).toContain('AirPods Pro 2')
  expect(about.textContent).toContain('7A305')
  expect(within(about).getByText('Serial number').closest('div')!.textContent).toContain('—')
})
