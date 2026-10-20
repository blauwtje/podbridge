import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { connectBridge, EMPTY, setSnapshot, useSnapshot } from './snapshot'

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
