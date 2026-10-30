export const POLL_INTERVAL_MS = 2500

/** One cancellable status check, with modest backoff after transient failures. */
export function schedulePoll(callback: () => void, options: { immediate?: boolean; failures?: number } = {}): () => void {
  const delay = options.immediate ? 0 : Math.min(5000, POLL_INTERVAL_MS + (options.failures ?? 0) * 800)
  const timer = window.setTimeout(callback, delay)
  return () => window.clearTimeout(timer)
}
