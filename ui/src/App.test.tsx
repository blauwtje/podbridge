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
  ['the app reports ok: false', () => Promise.resolve({ ok: false, error: 'no clipboard owner' }), 'no clipboard owner'],
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
