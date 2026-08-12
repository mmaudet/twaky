import { describe, it, expect } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import { server } from '../test/mocks/server'
import {
    useSpamDecisions,
    useSpamStats,
    useRestoreSpam,
} from './use-mail-sentinel-spam'

function makeWrapper() {
    const qc = new QueryClient({
        defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
        },
    })
    return function Wrapper({ children }: { children: ReactNode }) {
        return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    }
}

const SAMPLE_DECISION = {
    id: 'aaaaaaaa-0000-0000-0000-000000000001',
    email_id: 'email-1',
    thread_id: 'thread-1',
    sender_email: 'spammer@evil.com',
    subject: 'Win a prize!',
    received_at: '2026-08-01T10:00:00Z',
    bucket: 'spam',
    signal_source: 'rspamd_junk_keyword',
    score: null,
    reason: null,
    restored_at: null,
    restored_by: null,
    decided_at: '2026-08-01T10:01:00Z',
}

const SAMPLE_STATS = {
    spam: 10,
    newsletter: 3,
    phishing_alert: 1,
    restored: 2,
    total_processed: 14,
}

describe('useSpamDecisions', () => {
    it('test_list_returns_shape', async () => {
        server.use(
            http.get('http://localhost:3000/api/mail-sentinel/spam', () =>
                HttpResponse.json([SAMPLE_DECISION]),
            ),
        )
        const { result } = renderHook(() => useSpamDecisions(), {
            wrapper: makeWrapper(),
        })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        const decisions = result.current.data!
        expect(Array.isArray(decisions)).toBe(true)
        expect(decisions).toHaveLength(1)
        expect(decisions[0].id).toBe('aaaaaaaa-0000-0000-0000-000000000001')
        expect(decisions[0].bucket).toBe('spam')
        expect(decisions[0].sender_email).toBe('spammer@evil.com')
    })

    it('test_list_error_propagates', async () => {
        server.use(
            http.get('http://localhost:3000/api/mail-sentinel/spam', () =>
                HttpResponse.json(
                    { error: { code: 'internal_error', message: 'DB unavailable' } },
                    { status: 500 },
                ),
            ),
        )
        const { result } = renderHook(() => useSpamDecisions(), {
            wrapper: makeWrapper(),
        })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error?.message).toBe('DB unavailable')
    })
})

describe('useSpamStats', () => {
    it('test_stats_returns_shape', async () => {
        server.use(
            http.get('http://localhost:3000/api/mail-sentinel/spam/stats', () =>
                HttpResponse.json(SAMPLE_STATS),
            ),
        )
        const { result } = renderHook(() => useSpamStats(30), {
            wrapper: makeWrapper(),
        })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        const stats = result.current.data!
        expect(stats.spam).toBe(10)
        expect(stats.newsletter).toBe(3)
        expect(stats.phishing_alert).toBe(1)
        expect(stats.restored).toBe(2)
        expect(stats.total_processed).toBe(14)
    })
})

describe('useRestoreSpam', () => {
    it('test_restore_success_invalidates_lists', async () => {
        const qc = new QueryClient({
            defaultOptions: {
                queries: { retry: false },
                mutations: { retry: false },
            },
        })
        const invalidated: string[][] = []
        const origInvalidate = qc.invalidateQueries.bind(qc)
        qc.invalidateQueries = (filters) => {
            invalidated.push(
                (filters as { queryKey?: string[] }).queryKey ?? [],
            )
            return origInvalidate(filters)
        }
        function Wrapper({ children }: { children: ReactNode }) {
            return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
        }

        const restored = { ...SAMPLE_DECISION, restored_at: '2026-08-10T12:00:00Z', restored_by: 'me@x.com' }
        server.use(
            http.post(
                'http://localhost:3000/api/mail-sentinel/spam/:decision_id/restore',
                () => HttpResponse.json(restored),
            ),
        )

        const { result } = renderHook(() => useRestoreSpam(), { wrapper: Wrapper })
        act(() => {
            result.current.mutate('aaaaaaaa-0000-0000-0000-000000000001')
        })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))

        // Both query key families must be invalidated
        expect(invalidated).toContainEqual(['mail-spam-decisions'])
        expect(invalidated).toContainEqual(['mail-spam-stats'])
    })

    it('test_restore_409_already_propagates', async () => {
        server.use(
            http.post(
                'http://localhost:3000/api/mail-sentinel/spam/:decision_id/restore',
                () =>
                    HttpResponse.json(
                        { error: { code: 'already_restored', message: 'Email already restored' } },
                        { status: 409 },
                    ),
            ),
        )
        const { result } = renderHook(() => useRestoreSpam(), {
            wrapper: makeWrapper(),
        })
        act(() => {
            result.current.mutate('aaaaaaaa-0000-0000-0000-000000000001')
        })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error?.message).toBe('Email already restored')
    })

    it('test_restore_502_jmap_propagates', async () => {
        server.use(
            http.post(
                'http://localhost:3000/api/mail-sentinel/spam/:decision_id/restore',
                () =>
                    HttpResponse.json(
                        { error: { code: 'jmap_restore_failed', message: 'JMAP server unreachable' } },
                        { status: 502 },
                    ),
            ),
        )
        const { result } = renderHook(() => useRestoreSpam(), {
            wrapper: makeWrapper(),
        })
        act(() => {
            result.current.mutate('aaaaaaaa-0000-0000-0000-000000000001')
        })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error?.message).toBe('JMAP server unreachable')
    })
})
