import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  apiClient,
  apiRequest,
  buildUrl,
  resolveJacRedBaseUrl,
} from '@/lib/api/client'

vi.mock('@/lib/storage', () => ({
  getApiKey: () => 'api-secret',
  getDevKey: () => 'dev-secret',
}))

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', '')
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
})

describe('buildUrl', () => {
  it('encodes repeated array query params', () => {
    const url = buildUrl('https://example.test', '/api/v2.0/indexers/all/results', {
      query: 'matrix',
      'Category[]': ['2000', '5070'],
      year: 1999,
    })
    expect(url).toContain('query=matrix')
    expect(url).toContain('year=1999')
    expect(url).toContain('Category%5B%5D=2000')
    expect(url).toContain('Category%5B%5D=5070')
  })
})

describe('resolveJacRedBaseUrl', () => {
  it('prefers explicit baseUrl over env', () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://env.example')
    expect(resolveJacRedBaseUrl('https://explicit.example/path')).toBe(
      'https://explicit.example/path',
    )
  })

  it('uses env then empty same-origin', () => {
    expect(resolveJacRedBaseUrl()).toBe('')
    vi.stubEnv('VITE_API_BASE_URL', 'https://env.example/api')
    expect(resolveJacRedBaseUrl()).toBe('https://env.example')
  })
})

describe('apiRequest', () => {
  it('fetches public build source without either application credential', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ gitFullSha: 'a'.repeat(40) }), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const version = await apiClient.getVersion({ withApiKey: true, withDevKey: true })
    expect(version.gitFullSha).toBe('a'.repeat(40))
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.has('X-Api-Key')).toBe(false)
    expect(headers.has('X-Dev-Key')).toBe(false)
  })

  it('adds configured auth headers', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    await apiRequest('/test', {}, { withDevKey: true })

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.get('X-Api-Key')).toBe('api-secret')
    expect(headers.get('X-Dev-Key')).toBe('dev-secret')
  })

  it('uses VITE_API_BASE_URL for requests', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.com')
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    await apiRequest('/health')

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      'https://api.example.com/health',
    )
  })

  it('throws a typed error for non-success responses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ message: 'denied' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(apiRequest('/test')).rejects.toMatchObject({
      name: 'ApiError',
      status: 403,
      message: 'denied',
    })
  })

  it('honors caller cancellation', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('Aborted', 'AbortError')),
          )
        }),
    )
    const controller = new AbortController()
    const request = apiRequest('/test', {}, { signal: controller.signal })
    controller.abort()
    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
  })
})

describe('apiClient.getBackgroundJobs', () => {
  it('calls the public health jobs endpoint without auth headers', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ jobs: [] }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    await apiClient.getBackgroundJobs()

    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/health/background-jobs')
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.get('X-Api-Key')).toBeNull()
    expect(headers.get('X-Dev-Key')).toBeNull()
  })
})

describe('apiClient.getConf', () => {
  it('sends the stored key only in the X-Api-Key header', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({
          jacred: true,
          configured: false,
          apikey: true,
          version: '3.7.1-next+726f1544',
        }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    await apiClient.getConf()

    const called = String(fetchMock.mock.calls[0]?.[0])
    expect(called).toContain('/api/v1.0/conf')
    expect(called).not.toContain('api-secret')
    expect(called).not.toContain('apikey=')
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.get('X-Api-Key')).toBe('api-secret')
  })
})

describe('apiClient.getJackettResults', () => {
  it('calls Jackett v2 results with Category[] and stored apikey', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ Results: [] }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    await apiClient.getJackettResults('all', {
      query: 'matrix',
      'Category[]': ['2000', '5000'],
      is_serial: 1,
    })

    const called = String(fetchMock.mock.calls[0]?.[0])
    expect(called).toContain('/api/v2.0/indexers/all/results')
    expect(called).toContain('query=matrix')
    expect(called).toContain('is_serial=1')
    expect(called).toContain('Category%5B%5D=2000')
    expect(called).toContain('Category%5B%5D=5000')
    expect(called).not.toContain('api-secret')
    expect(called).not.toContain('apikey=')
    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.get('X-Api-Key')).toBe('api-secret')
  })
})
