import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, expectTypeOf, test, vi } from 'vitest'
import { connectBridge, EMPTY, setSnapshot, useSnapshot, type PbApi } from './snapshot'

function Probe() {
  return <p>{useSnapshot().steps.length}</p>
}

afterEach(() => {
  cleanup()
  setSnapshot(EMPTY)
})

test('window.__pb.emit updates subscribers', () => {
  connectBridge()
  render(<Probe />)
  expect(screen.getByText('0')).toBeTruthy()
  act(() => window.__pb!.emit({ ...EMPTY, steps: [{ name: 'checks', status: 'done' }] }))
  expect(screen.getByText('1')).toBeTruthy()
})

test('loads get_snapshot once when pywebview is ready', async () => {
  const get_snapshot = vi.fn().mockResolvedValue({ ...EMPTY, steps: [{ name: 'driver', status: 'active' }] })
  window.pywebview = { api: { get_snapshot } as never }
  render(<Probe />)
  await act(async () => connectBridge())
  expect(get_snapshot).toHaveBeenCalledTimes(1)
  expect(screen.getByText('1')).toBeTruthy()
})

test('a failed get_snapshot shows an error and keeps the last snapshot', async () => {
  const last = { ...EMPTY, steps: [{ name: 'driver' as const, status: 'done' as const }] }
  setSnapshot(last)
  const get_snapshot = vi.fn().mockRejectedValue(new Error('bridge went away'))
  window.pywebview = { api: { get_snapshot } as never }
  await act(async () => connectBridge())
  let seen: ReturnType<typeof useSnapshot> | undefined
  function Grab() {
    seen = useSnapshot()
    return null
  }
  render(<Grab />)
  expect(seen!.steps).toEqual(last.steps)
  expect(seen!.error?.details).toContain('bridge went away')
})

test('copy_details is typed as the text to copy', () => {
  const api: PbApi = { get_snapshot: vi.fn(), set_setting: vi.fn(), copy_details: async () => 'text' }
  expectTypeOf(api.copy_details).returns.resolves.toBeString()
})
