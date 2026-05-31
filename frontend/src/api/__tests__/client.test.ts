import { describe, it, expect, beforeEach, vi } from 'vitest'
import { apiClient, ApiError, API_UNAUTHORIZED_EVENT } from '@/api/client'

// ── Helpers ───────────────────────────────────────────────────────────────────

function mockResponse(status: number, body: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('apiClient', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('GET resolves with parsed JSON on 200', async () => {
    const data = { id: 'abc', type: 'page' }
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(200, data))

    const result = await apiClient.get('/api/blocks/abc')
    expect(result).toEqual(data)
  })

  it('POST sends body as JSON and resolves with parsed response', async () => {
    const payload = { type: 'page', parent_id: 'ws-id' }
    const created = { id: 'new', ...payload }
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(201, created))

    const result = await apiClient.post('/api/blocks', payload)
    expect(result).toEqual(created)

    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(payload)
  })

  it('PATCH sends correct method', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(200, {}))
    await apiClient.patch('/api/blocks/abc', { content: {} })
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).method).toBe('PATCH')
  })

  it('DELETE sends correct method', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(200, { affected: [] }))
    await apiClient.delete('/api/blocks/abc')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).method).toBe('DELETE')
  })

  it('204 No Content resolves with undefined', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 204,
      json: async () => { throw new Error('no body') },
    } as unknown as Response)

    const result = await apiClient.post('/api/blocks/x/purge')
    expect(result).toBeUndefined()
  })

  it('throws ApiError with status on non-OK response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      mockResponse(404, { detail: 'Block not found' }),
    )

    await expect(apiClient.get('/api/blocks/missing')).rejects.toMatchObject({
      status: 404,
      message: 'Block not found',
    })
  })

  it('ApiError instance has correct name', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(500, { detail: 'oops' }))

    try {
      await apiClient.get('/api/blocks/x')
    } catch (e) {
      expect((e as ApiError).name).toBe('ApiError')
    }
  })

  it('throws ApiError with status 0 on network failure', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('Failed to fetch'))

    await expect(apiClient.get('/api/blocks/x')).rejects.toMatchObject({
      status: 0,
    })
  })

  it('dispatches unauthorized event on 401', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(401, { detail: 'Nicht angemeldet' }))

    const listener = vi.fn()
    window.addEventListener(API_UNAUTHORIZED_EVENT, listener)

    try {
      await apiClient.get('/api/blocks/x')
    } catch {
      // expected
    }

    expect(listener).toHaveBeenCalledOnce()
    window.removeEventListener(API_UNAUTHORIZED_EVENT, listener)
  })

  it('throws ApiError with status 401 on 401 response', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(401))

    await expect(apiClient.get('/api/blocks/x')).rejects.toMatchObject({
      status: 401,
    })
  })

  it('always includes credentials: include', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(mockResponse(200, {}))
    await apiClient.get('/api/blocks/x')
    const [, init] = vi.mocked(fetch).mock.calls[0]
    expect((init as RequestInit).credentials).toBe('include')
  })
})
