import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest } from '../api/client'
import { safeExternalUrl } from '../utils/safety'

afterEach(() => vi.unstubAllGlobals())

describe('URL safety', () => {
  it('permits only HTTP and HTTPS external links', () => {
    expect(safeExternalUrl('https://example.com/path')).toBe('https://example.com/path')
    expect(safeExternalUrl('http://example.com/')).toBe('http://example.com/')
    for (const value of ['javascript:alert(1)', 'data:text/html,hello', 'file:///secret', '//example.com', 'not a url']) {
      expect(safeExternalUrl(value)).toBeNull()
    }
  })
})

describe('API errors', () => {
  it('exposes 404 details without stack traces', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Graph snapshot not found' }), { status: 404, statusText: 'Not Found' })))
    await expect(apiRequest('/api/example')).rejects.toEqual(new ApiError(404, 'Graph snapshot not found'))
  })
  it('turns network errors into a useful message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('secret network internals')))
    await expect(apiRequest('/api/example')).rejects.toThrow('Cannot reach SpiderMind')
  })
})
