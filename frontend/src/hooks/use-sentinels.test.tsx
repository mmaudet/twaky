import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import {
    useSentinels, useSentinel, usePatchSentinel,
    useSentinelRuns, useSentinelRunDetail,
} from './use-sentinels'

const server = setupServer()

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function makeWrapper() {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return function Wrapper({ children }: { children: ReactNode }) {
        return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    }
}

const MAIL_SUMMARY = {
    name: 'mail',
    display_name: 'Mail sentinel',
    enabled: true,
    version: '1.0.0',
    stats_24h: { total: 47, errors: 0 },
}

const MAIL_DETAIL = {
    name: 'mail',
    display_name: 'Mail sentinel',
    description: 'Autonomous email triage.',
    enabled: true,
    version: '1.0.0',
    config_schema: {},
    config_values: { memory_candidate_pool: 100 },
    created_at: '2026-08-10T00:00:00Z',
    updated_at: '2026-08-10T00:00:00Z',
}

const RUN_SUMMARY = {
    id: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    sentinel_name: 'mail',
    event_ref: 'jmap:acc1:msg-42',
    started_at: '2026-08-10T10:00:00Z',
    completed_at: '2026-08-10T10:00:05Z',
    duration_ms: 5000,
    outcome: 'processed',
    mission_id: null,
    llm_calls: 2,
}

describe('useSentinels', () => {
    it('returns a list of sentinel summaries', async () => {
        server.use(
            http.get('http://localhost:3000/api/sentinels', () =>
                HttpResponse.json([MAIL_SUMMARY]),
            ),
        )
        const { result } = renderHook(() => useSentinels(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toHaveLength(1)
        expect(result.current.data![0].name).toBe('mail')
        expect(result.current.data![0].stats_24h.total).toBe(47)
    })

    it('propagates API errors', async () => {
        server.use(
            http.get('http://localhost:3000/api/sentinels', () =>
                HttpResponse.json(
                    { error: { code: 'http_401', message: 'Not authenticated' } },
                    { status: 401 },
                ),
            ),
        )
        const { result } = renderHook(() => useSentinels(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error?.message).toBe('Not authenticated')
    })
})

describe('useSentinel', () => {
    it('fetches a single sentinel by name', async () => {
        server.use(
            http.get('http://localhost:3000/api/sentinels/mail', () =>
                HttpResponse.json(MAIL_DETAIL),
            ),
        )
        const { result } = renderHook(() => useSentinel('mail'), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.description).toContain('triage')
        expect(result.current.data!.config_values).toHaveProperty('memory_candidate_pool')
    })
})

describe('usePatchSentinel', () => {
    it('sends PATCH and returns updated sentinel detail', async () => {
        server.use(
            http.patch('http://localhost:3000/api/sentinels/mail', async ({ request }) => {
                const body = (await request.clone().json()) as { enabled?: boolean }
                return HttpResponse.json({ ...MAIL_DETAIL, enabled: body.enabled ?? true })
            }),
        )
        const { result } = renderHook(() => usePatchSentinel('mail'), { wrapper: makeWrapper() })
        act(() => { result.current.mutate({ enabled: false }) })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.enabled).toBe(false)
    })

    it('invalidates sentinels and sentinels/name queries on success', async () => {
        const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
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
            http.patch('http://localhost:3000/api/sentinels/mail', () =>
                HttpResponse.json(MAIL_DETAIL),
            ),
        )
        const { result } = renderHook(() => usePatchSentinel('mail'), { wrapper: Wrapper })
        act(() => { result.current.mutate({ enabled: true }) })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(invalidated).toContainEqual(['sentinels'])
        expect(invalidated).toContainEqual(['sentinels', 'mail'])
    })

    it('propagates patch errors', async () => {
        server.use(
            http.patch('http://localhost:3000/api/sentinels/mail', () =>
                HttpResponse.json(
                    { error: { code: 'validation_failed', message: 'Empty body' } },
                    { status: 422 },
                ),
            ),
        )
        const { result } = renderHook(() => usePatchSentinel('mail'), { wrapper: makeWrapper() })
        act(() => { result.current.mutate({}) })
        await waitFor(() => expect(result.current.isError).toBe(true))
        expect(result.current.error?.message).toBe('Empty body')
    })
})

describe('useSentinelRuns', () => {
    it('fetches runs for a sentinel', async () => {
        server.use(
            http.get('http://localhost:3000/api/sentinels/mail/runs', () =>
                HttpResponse.json([RUN_SUMMARY]),
            ),
        )
        const { result } = renderHook(
            () => useSentinelRuns('mail'),
            { wrapper: makeWrapper() },
        )
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toHaveLength(1)
        expect(result.current.data![0].event_ref).toBe('jmap:acc1:msg-42')
    })

    it('passes limit and before query params', async () => {
        let capturedUrl = ''
        server.use(
            http.get('http://localhost:3000/api/sentinels/mail/runs', ({ request }) => {
                capturedUrl = request.url
                return HttpResponse.json([])
            }),
        )
        const { result } = renderHook(
            () => useSentinelRuns('mail', { limit: 10, before: '2026-08-10T10:00:00Z' }),
            { wrapper: makeWrapper() },
        )
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(capturedUrl).toContain('limit=10')
        expect(capturedUrl).toContain('before=')
    })
})

describe('useSentinelRunDetail', () => {
    it('fetches a single run by id', async () => {
        const detail = {
            ...RUN_SUMMARY,
            trace: [{ node: 'load_thread', ts: '2026-08-10T10:00:01Z' }],
            error_repr: null,
        }
        server.use(
            http.get(
                'http://localhost:3000/api/sentinels/runs/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                () => HttpResponse.json(detail),
            ),
        )
        const { result } = renderHook(
            () => useSentinelRunDetail('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'),
            { wrapper: makeWrapper() },
        )
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.trace).toHaveLength(1)
        expect(result.current.data!.trace[0]).toMatchObject({ node: 'load_thread' })
    })
})
