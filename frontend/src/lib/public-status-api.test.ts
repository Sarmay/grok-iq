import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api'

function jsonResponse() {
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function authorization(init: RequestInit | undefined) {
  return new Headers(init?.headers).get('Authorization')
}

describe('public status requests', () => {
  afterEach(() => {
    useAuthStore.getState().auth.reset()
    vi.unstubAllGlobals()
  })

  it('sends the admin token so Linux DO is not required', async () => {
    useAuthStore.setState((state) => ({
      auth: { ...state.auth, accessToken: 'admin-token' },
    }))
    const fetchMock = vi.fn(async () => jsonResponse())
    vi.stubGlobal('fetch', fetchMock)

    await api.publicUpstreamUsage({ period: '24h', timezone: 'Asia/Shanghai' })
    await api.publicUpstreamAccounts()
    await api.lookupPublicClientKeyQuota('sk-test')
    await api.lookupPublicClientKeyUsage({
      apiKey: 'sk-test',
      period: '24h',
    })

    expect(fetchMock).toHaveBeenCalledTimes(4)
    for (const [url, init] of fetchMock.mock.calls) {
      expect(String(url)).toMatch(/\/public\//)
      expect(authorization(init)).toBe('Bearer admin-token')
    }
  })

  it('leaves the admin token off when nobody is signed in', async () => {
    const fetchMock = vi.fn(async () => jsonResponse())
    vi.stubGlobal('fetch', fetchMock)

    await api.publicUpstreamUsage({ period: '7d' })

    expect(authorization(fetchMock.mock.calls[0][1])).toBeNull()
  })
})
