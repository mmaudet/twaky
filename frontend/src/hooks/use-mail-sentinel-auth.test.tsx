import { describe, it, expect } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import { server } from '../test/mocks/server'
import {
    useMailSentinelAuth,
    useForceRefresh,
    useDisconnect,
} from './use-mail-sentinel-auth'

function makeWrapper() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
    return function Wrapper({ children }: { children: ReactNode }) {
        return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    }
}

const DISCONNECTED_STATUS = {
    connected: false,
    provider: null,
    account_email: null,
    account_name: null,
    session_url: null,
    access_token_expires_at: null,
    last_refresh_at: null,
    last_refresh_error: null,
}

const CONNECTED_STATUS = {
    connected: true,
    provider: 'fastmail',
    account_email: 'me@x.com',
    account_name: 'Me X',
    session_url: 'https://jmap.fastmail.com',
    access_token_expires_at: '2026-08-10T12:00:00Z',
    last_refresh_at: '2026-08-10T11:00:00Z',
    last_refresh_error: null,
}

describe('useMailSentinelAuth', () => {
    it('test_returns_disconnected_state', async () => {
        server.use(
            http.get('http://localhost:3000/api/mail-sentinel/auth', () =>
                HttpResponse.json(DISCONNECTED_STATUS),
            ),
        )
        const { result } = renderHook(() => useMailSentinelAuth(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.connected).toBe(false)
        expect(result.current.data!.account_email).toBeNull()
    })

    it('test_returns_connected_state', async () => {
        server.use(
            http.get('http://localhost:3000/api/mail-sentinel/auth', () =>
                HttpResponse.json(CONNECTED_STATUS),
            ),
        )
        const { result } = renderHook(() => useMailSentinelAuth(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.connected).toBe(true)
        expect(result.current.data!.account_email).toBe('me@x.com')
        expect(result.current.data!.provider).toBe('fastmail')
    })

    it('test_query_error_propagates', async () => {
        server.use(
            http.get('http://localhost:3000/api/mail-sentinel/auth', () =>
                HttpResponse.json(
                    { error: { code: 'internal_error', message: 'Service unavailable' } },
                    { status: 500 },
                ),
            ),
        )
        const { result } = renderHook(() => useMailSentinelAuth(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error?.message).toBe('Service unavailable')
    })
})

describe('useForceRefresh', () => {
    it('test_refresh_mutation_invalidates_status', async () => {
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
        const invalidated: string[][] = []
        const origInvalidate = qc.invalidateQueries.bind(qc)
        qc.invalidateQueries = (filters) => {
            invalidated.push((filters as { queryKey?: string[] }).queryKey ?? [])
            return origInvalidate(filters)
        }
        function Wrapper({ children }: { children: ReactNode }) {
            return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
        }
        server.use(
            http.post('http://localhost:3000/api/mail-sentinel/auth/refresh', () =>
                HttpResponse.json(CONNECTED_STATUS),
            ),
        )
        const { result } = renderHook(() => useForceRefresh(), { wrapper: Wrapper })
        act(() => { result.current.mutate() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(invalidated).toContainEqual(['mail-sentinel-auth'])
    })

    it('test_refresh_502_propagates_error', async () => {
        server.use(
            http.post('http://localhost:3000/api/mail-sentinel/auth/refresh', () =>
                HttpResponse.json(
                    { error: { code: 'refresh_failed', message: 'invalid_grant' } },
                    { status: 502 },
                ),
            ),
        )
        const { result } = renderHook(() => useForceRefresh(), { wrapper: makeWrapper() })
        act(() => { result.current.mutate() })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error?.message).toBe('invalid_grant')
    })
})

describe('useDisconnect', () => {
    it('test_disconnect_mutation_invalidates_status', async () => {
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
        const invalidated: string[][] = []
        const origInvalidate = qc.invalidateQueries.bind(qc)
        qc.invalidateQueries = (filters) => {
            invalidated.push((filters as { queryKey?: string[] }).queryKey ?? [])
            return origInvalidate(filters)
        }
        function Wrapper({ children }: { children: ReactNode }) {
            return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
        }
        server.use(
            http.delete('http://localhost:3000/api/mail-sentinel/auth', () =>
                new HttpResponse(null, { status: 204 }),
            ),
        )
        const { result } = renderHook(() => useDisconnect(), { wrapper: Wrapper })
        act(() => { result.current.mutate() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(invalidated).toContainEqual(['mail-sentinel-auth'])
    })
})
