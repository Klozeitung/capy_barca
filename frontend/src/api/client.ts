/**
 * Typed HTTP client for the CapyBarca API.
 *
 * All fetch traffic from the store layer flows through here. This gives us
 * a single place to enforce:
 *   - consistent Content-Type and credentials headers
 *   - typed ApiError objects (status + message) instead of raw strings
 *   - centralised 401 handling via a custom DOM event so the auth store
 *     can react without a circular import
 *
 * Usage:
 *   import { apiClient } from '@/api/client'
 *   const block = await apiClient.get<Block>('/api/blocks/123')
 *   const created = await apiClient.post<Block>('/api/blocks', payload)
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
 * Execute a fetch, parse the response, and map non-OK statuses to ApiError.
 *
 * A 401 additionally broadcasts the `capybarca:unauthorized` event so that
 * the auth store can react globally.
 */
async function request<T>(url: string, init: RequestInit): Promise<T> {
  let response: Response

  try {
    response = await fetch(url, init)
  } catch (networkError) {
    throw new ApiError(0, `Network error: ${(networkError as Error).message}`)
  }

  if (response.status === 401) {
    broadcastUnauthorized()
    throw new ApiError(401, 'Nicht angemeldet')
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // ignore – use the fallback detail string
    }
    throw new ApiError(response.status, detail)
  }

  // 204 No Content – return undefined cast to T (callers should type as void)
  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
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
}
