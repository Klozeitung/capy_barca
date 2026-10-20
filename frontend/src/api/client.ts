/**
 * Typed HTTP client for the CapyBarca API.
 *
 * All fetch traffic from the store layer flows through here. This gives us
 * a single place to enforce:
 *   - consistent Content-Type and credentials headers
 *   - typed ApiError objects (status + message) instead of raw strings
 *   - centralised 401 handling via a custom DOM event so the auth store
 *     can react without a circular import
 *   - a second event for changes refused because the caller may only read
 *     the block, so the user learns why the change did not stick
 *
 * Usage:
 *   import { apiClient } from '@/api/client'
 *   const block = await apiClient.get<Block>('/api/blocks/123')
 *   const created = await apiClient.post<Block>('/api/blocks', payload)
 *   const uploaded = await apiClient.postForm<File>('/api/media/upload/…', form)
 *   const bytes = await apiClient.getBlob('/media/…')
 */

// ── Error type ────────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// ── 401 broadcast ─────────────────────────────────────────────────────────────

/**
 * Event name dispatched on `window` when any API call receives a 401.
 * The auth store subscribes to this to clear local auth state without
 * a circular import between the store and the client.
 */
export const API_UNAUTHORIZED_EVENT = 'capybarca:unauthorized'

function broadcastUnauthorized(): void {
  window.dispatchEvent(new CustomEvent(API_UNAUTHORIZED_EVENT))
}

// ── Read-only broadcast ───────────────────────────────────────────────────────

/**
 * Event name dispatched on `window` when a change is refused because the
 * caller has read access only. ``NoticeToasts`` turns it into a notice.
 */
export const API_READ_ONLY_EVENT = 'capybarca:read-only'

/**
 * The detail the backend sends with such a refusal (``READ_ONLY_DETAIL`` in
 * ``app.session.deps``). A 403 without it means no access at all.
 */
export const READ_ONLY_DETAIL = 'Read-only access'

function broadcastReadOnly(): void {
  window.dispatchEvent(new CustomEvent(API_READ_ONLY_EVENT))
}

// ── Request helpers ───────────────────────────────────────────────────────────

type JsonBody = Record<string, unknown> | unknown[] | null

/**
 * Build a base RequestInit with the standard CapyBarca headers.
 * Credentials are always included so the session cookie is forwarded.
 */
function baseInit(method: string, body?: JsonBody): RequestInit {
  const init: RequestInit = {
    method,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) {
    init.body = JSON.stringify(body)
  }
  return init
}

/**
 * Build a RequestInit for a multipart upload. No Content-Type header is set,
 * so the browser adds the multipart boundary itself.
 */
function formInit(form: FormData): RequestInit {
  return { method: 'POST', credentials: 'include', body: form }
}

/** Read a successful response as JSON. */
function parseJson<T>(response: Response): Promise<T> {
  return response.json() as Promise<T>
}

/**
 * Execute a fetch, parse the response, and map non-OK statuses to ApiError.
 *
 * A 401 additionally broadcasts the `capybarca:unauthorized` event so that
 * the auth store can react globally. A 403 with the read-only detail
 * broadcasts `capybarca:read-only`; the error is thrown either way, so the
 * caller still rolls back its optimistic change.
 *
 * *parse* reads the body of a successful response; JSON unless the caller
 * asks for something else, such as the bytes of a download.
 */
async function request<T>(
  url: string,
  init: RequestInit,
  parse: (response: Response) => Promise<T> = parseJson,
): Promise<T> {
  let response: Response

  try {
    response = await fetch(url, init)
  } catch (networkError) {
    throw new ApiError(0, `Network error: ${(networkError as Error).message}`)
  }

  if (response.status === 401) {
    broadcastUnauthorized()
    throw new ApiError(401, 'Not authenticated')
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // ignore – use the fallback detail string
    }
    if (response.status === 403 && detail === READ_ONLY_DETAIL) {
      broadcastReadOnly()
    }
    throw new ApiError(response.status, detail)
  }

  // 204 No Content – return undefined cast to T (callers should type as void)
  if (response.status === 204) {
    return undefined as T
  }

  return parse(response)
}

// ── Public client ─────────────────────────────────────────────────────────────

export const apiClient = {
  get<T>(url: string): Promise<T> {
    return request<T>(url, baseInit('GET'))
  },

  post<T>(url: string, body?: JsonBody): Promise<T> {
    return request<T>(url, baseInit('POST', body))
  },

  patch<T>(url: string, body: JsonBody): Promise<T> {
    return request<T>(url, baseInit('PATCH', body))
  },

  put<T>(url: string, body: JsonBody): Promise<T> {
    return request<T>(url, baseInit('PUT', body))
  },

  delete<T>(url: string): Promise<T> {
    return request<T>(url, baseInit('DELETE'))
  },

  /** POST a multipart form, for file uploads. */
  postForm<T>(url: string, form: FormData): Promise<T> {
    return request<T>(url, formInit(form))
  },

  /** GET a response body as raw bytes, for downloads. */
  getBlob(url: string): Promise<Blob> {
    return request<Blob>(url, { method: 'GET', credentials: 'include' }, (r) => r.blob())
  },
}
