export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch { return null }
}

export function shortId(value: string, length = 10): string {
  const id = value.split(':').at(-1) ?? value
  return id.length > length ? `${id.slice(0, length)}…` : id
}
