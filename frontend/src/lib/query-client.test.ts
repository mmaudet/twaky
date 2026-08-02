import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { createElement } from 'react'
import { useQuery } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/mocks/server'
import { createQueryClient } from './query-client'

// ── helpers ──────────────────────────────────────────────────────────────────

/** Wrap a renderHook call in the query client under test (not a plain QueryClient). */
function wrapper({ children }: { children: React.ReactNode }) {
    return createElement(QueryClientProvider, { client: createQueryClient() }, children)
}

// ── tests ─────────────────────────────────────────────────────────────────────

describe('createQueryClient — QueryCache 401 handler', () => {
    const originalHref = window.location.href

    beforeEach(() => {
        // Allow window.location.href writes in jsdom
        Object.defineProperty(window, 'location', {
            writable: true,
            value: { ...window.location, href: originalHref },
        })
    })

    afterEach(() => {
        vi.restoreAllMocks()
    })

    it('redirects to /api/oauth/login when a query receives a 401', async () => {
        // Make /api/missions return 401 with the error envelope
        server.use(
            http.get('/api/missions', () =>
                HttpResponse.json(
                    { error: { code: 'http_401', message: 'unauthorized' } },
                    { status: 401 },
                ),
            ),
        )

        const { result } = renderHook(
            () =>
                useQuery({
                    queryKey: ['missions-401-test'],
                    queryFn: async () => {
                        const res = await fetch('/api/missions')
                        if (!res.ok) {
                            const body = (await res.json()) as {
                                error: { code: string; message: string }
                            }
                            // Replicate what api.GET does: throw ApiError on 4xx
                            const { ApiError } = await import('./api-error')
                            throw new ApiError(body)
                        }
                        return res.json()
                    },
                    retry: false,
                }),
            { wrapper },
        )

        await waitFor(() => expect(result.current.isError).toBe(true))

        // The QueryCache onError handler should have set location.href
        expect(window.location.href).toMatch(/\/api\/oauth\/login\?return_to=/)
    })

    it('does NOT redirect on non-401 query errors', async () => {
        server.use(
            http.get('/api/missions', () =>
                HttpResponse.json(
                    { error: { code: 'http_404', message: 'not found' } },
                    { status: 404 },
                ),
            ),
        )

        const { result } = renderHook(
            () =>
                useQuery({
                    queryKey: ['missions-404-test'],
                    queryFn: async () => {
                        const res = await fetch('/api/missions')
                        if (!res.ok) {
                            const body = (await res.json()) as {
                                error: { code: string; message: string }
                            }
                            const { ApiError } = await import('./api-error')
                            throw new ApiError(body)
                        }
                        return res.json()
                    },
                    retry: false,
                }),
            { wrapper },
        )

        await waitFor(() => expect(result.current.isError).toBe(true))

        // href must NOT have been changed to a login redirect
        expect(window.location.href).not.toMatch(/\/api\/oauth\/login/)
    })
})
