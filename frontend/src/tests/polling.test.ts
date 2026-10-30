import { afterEach, describe, expect, it, vi } from 'vitest'
import { schedulePoll } from '../features/research/polling'

afterEach(() => vi.useRealTimers())

describe('cancellable status polling', () => {
  it('waits at least 2.5 seconds and cancels cleanly on unmount', () => {
    vi.useFakeTimers()
    const status = vi.fn()
    const cancel = schedulePoll(status)
    vi.advanceTimersByTime(2499)
    expect(status).not.toHaveBeenCalled()
    cancel()
    vi.advanceTimersByTime(5000)
    expect(status).not.toHaveBeenCalled()
  })
  it('backs off transient status failures without exceeding five seconds', () => {
    vi.useFakeTimers()
    const status = vi.fn()
    schedulePoll(status, { failures: 10 })
    vi.advanceTimersByTime(4999)
    expect(status).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(status).toHaveBeenCalledTimes(1)
  })
})
