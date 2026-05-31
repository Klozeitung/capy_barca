/**
 * useDownload
 *
 * Programmatic file download via fetch → Blob → object-URL anchor click.
 *
 * Using a plain <a :href="url" :download="name"> fails when the file is
 * served through an nginx proxy (the browser sees the UUID-based path in the
 * URL and ignores the download attribute).  Fetching the bytes ourselves and
 * creating a temporary blob URL guarantees that the browser uses the filename
 * we provide, independent of server-side headers or URL structure.
 */

export async function downloadFile(url: string, filename: string): Promise<void> {
  try {
    const res = await fetch(url, { credentials: 'include' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const blobUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    // Revoke after a short delay so the browser has time to start the download
    setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000)
  } catch {
    // Fallback: direct navigation (filename may be wrong but the file still downloads)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
  }
}
