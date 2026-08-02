import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import { useAgents, useAgent, useUpdateAgent, useDefaultPrompt } from './use-agents'

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

describe('useAgents', () => {
    it('returns 4 agent summaries on success', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents', () =>
                HttpResponse.json([
                    { id: 'atlas', display_name: 'Atlas', role: 'orchestrator', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                    { id: 'chronos', display_name: 'Chronos', role: 'specialist', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                    { id: 'iris', display_name: 'Iris', role: 'specialist', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                    { id: 'plume', display_name: 'Plume', role: 'specialist', model: null, temperature: null, effective_model: 'default', updated_at: '2026-01-01T00:00:00Z' },
                ]),
            ),
        )
        const { result } = renderHook(() => useAgents(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toHaveLength(4)
    })
})

describe('useAgent', () => {
    it('returns one agent', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents/plume', () =>
                HttpResponse.json({
                    id: 'plume', display_name: 'Plume', role: 'specialist',
                    system_prompt: 'you are plume', model: null, temperature: null,
                    effective_model: 'default', updated_at: '2026-01-01T00:00:00Z',
                }),
            ),
        )
        const { result } = renderHook(() => useAgent('plume'), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.system_prompt).toBe('you are plume')
    })

    it('surfaces 404 as ApiError', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents/zeus', () =>
                HttpResponse.json({ error: { code: 'agent_not_found', message: 'no' } }, { status: 404 }),
            ),
        )
        const { result } = renderHook(() => useAgent('zeus'), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isError).toBe(true))
        const error = result.current.error
        expect(error).not.toBeNull()
        expect((error as unknown as { envelope: { error: { code: string } } }).envelope.error.code).toBe('agent_not_found')
    })
})

describe('useDefaultPrompt', () => {
    it('does not fetch by default', () => {
        const { result } = renderHook(() => useDefaultPrompt('plume'), { wrapper: makeWrapper() })
        expect(result.current.fetchStatus).toBe('idle')
    })

    it('fetches when enabled=true', async () => {
        server.use(
            http.get('http://localhost:3000/api/agents/plume/default_prompt', () =>
                HttpResponse.json({ system_prompt: 'ORIGINAL PLUME PROMPT' }),
            ),
        )
        const { result } = renderHook(
            () => useDefaultPrompt('plume', { enabled: true }),
            { wrapper: makeWrapper() },
        )
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.system_prompt).toBe('ORIGINAL PLUME PROMPT')
    })
})

describe('useUpdateAgent', () => {
    it('sends PATCH and returns updated agent', async () => {
        server.use(
            http.patch('http://localhost:3000/api/agents/plume', async ({ request }) => {
                const clonedRequest = request.clone()
                const body = (await clonedRequest.json()) as { temperature?: number }
                return HttpResponse.json({
                    id: 'plume', display_name: 'Plume', role: 'specialist',
                    system_prompt: 'you are plume', model: null,
                    temperature: body.temperature,
                    effective_model: 'default', updated_at: '2026-01-01T00:00:00Z',
                })
            }),
        )
        const { result } = renderHook(() => useUpdateAgent('plume'), { wrapper: makeWrapper() })
        result.current.mutate({ temperature: 0.7 })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data?.temperature).toBe(0.7)
    })

    it('surfaces 422 as ApiError', async () => {
        server.use(
            http.patch('http://localhost:3000/api/agents/plume', () =>
                HttpResponse.json(
                    { error: { code: 'validation_failed', message: 'bad' } },
                    { status: 422 },
                ),
            ),
        )
        const { result } = renderHook(() => useUpdateAgent('plume'), { wrapper: makeWrapper() })
        result.current.mutate({ temperature: 99 })
        await waitFor(() => expect(result.current.isError).toBe(true))
        const error = result.current.error
        expect(error).not.toBeNull()
        expect((error as unknown as { envelope: { error: { code: string } } }).envelope.error.code).toBe('validation_failed')
    })
})
