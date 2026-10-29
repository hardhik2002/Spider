export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError' }
}

const configuredBase = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? ''

export async function apiRequest<T>(path: string, init: RequestInit = {}, timeoutMs = 12000): Promise<T> {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${configuredBase}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    })
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`
      try {
        const payload: unknown = await response.json()
        if (typeof payload === 'object' && payload !== null && 'detail' in payload) {
          const detail = payload.detail
          message = typeof detail === 'string' ? detail : JSON.stringify(detail)
        }
      } catch { /* keep HTTP status */ }
      throw new ApiError(response.status, message)
    }
    return await response.json() as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (error instanceof Error && error.name === 'AbortError') throw new ApiError(0, 'Request timed out. Check the backend and try again.')
    throw new ApiError(0, 'Cannot reach SpiderMind. Start the backend and try again.')
  } finally {
    clearTimeout(timeout)
  }
}
