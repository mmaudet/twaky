import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import {
    useSkills, useSkill, useCreateSkill, useUpdateSkill,
    useDeleteSkill, useTestSkill,
} from './use-skills'

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

describe('useSkills', () => {
    it('returns a list of skill summaries', async () => {
        server.use(
            http.get('http://localhost:3000/api/skills', () =>
                HttpResponse.json([
                    {
                        id: '11111111-1111-1111-1111-111111111111', name: 'echo',
                        description: 'e', bound_agents: ['atlas'], enabled: true,
                        created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
                    },
                ]),
            ),
        )
        const { result } = renderHook(() => useSkills(), { wrapper: makeWrapper() })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data).toHaveLength(1)
        expect(result.current.data![0].name).toBe('echo')
    })
})

describe('useSkill', () => {
    it('fetches a single skill by id', async () => {
        server.use(
            http.get('http://localhost:3000/api/skills/11111111-1111-1111-1111-111111111111', () =>
                HttpResponse.json({
                    id: '11111111-1111-1111-1111-111111111111', name: 'echo', description: 'e',
                    python_source: 'def run(**k): return 1',
                    config_schema: {}, config_values: {}, bound_agents: ['atlas'],
                    enabled: true,
                    created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
                }),
            ),
        )
        const { result } = renderHook(
            () => useSkill('11111111-1111-1111-1111-111111111111'),
            { wrapper: makeWrapper() },
        )
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.python_source).toContain('def run')
    })

    it('is disabled for id="new"', () => {
        const { result } = renderHook(() => useSkill('new'), { wrapper: makeWrapper() })
        expect(result.current.fetchStatus).toBe('idle')
    })
})

describe('useCreateSkill', () => {
    it('posts and returns the new skill', async () => {
        server.use(
            http.post('http://localhost:3000/api/skills', async ({ request }) => {
                const body = (await request.clone().json()) as { name: string }
                return HttpResponse.json({
                    id: '22222222-2222-2222-2222-222222222222',
                    name: body.name, description: 'x', python_source: 'def run(): pass',
                    config_schema: {}, config_values: {}, bound_agents: [], enabled: true,
                    created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
                }, { status: 201 })
            }),
        )
        const { result } = renderHook(() => useCreateSkill(), { wrapper: makeWrapper() })
        result.current.mutate({
            name: 'newone', description: 'x',
            python_source: 'def run(): pass', enabled: true,
        })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.name).toBe('newone')
    })
})

describe('useUpdateSkill', () => {
    it('sends PATCH and returns updated skill', async () => {
        server.use(
            http.patch('http://localhost:3000/api/skills/11111111-1111-1111-1111-111111111111', async ({ request }) => {
                const body = (await request.clone().json()) as { description?: string }
                return HttpResponse.json({
                    id: '11111111-1111-1111-1111-111111111111', name: 'echo',
                    description: body.description ?? 'e',
                    python_source: 'x', config_schema: {}, config_values: {},
                    bound_agents: [], enabled: true,
                    created_at: '2026-08-02T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
                })
            }),
        )
        const { result } = renderHook(
            () => useUpdateSkill('11111111-1111-1111-1111-111111111111'),
            { wrapper: makeWrapper() },
        )
        result.current.mutate({ description: 'updated' })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.description).toBe('updated')
    })
})

describe('useDeleteSkill', () => {
    it('sends DELETE and resolves without a body', async () => {
        server.use(
            http.delete('http://localhost:3000/api/skills/11111111-1111-1111-1111-111111111111', () =>
                new HttpResponse(null, { status: 204 }),
            ),
        )
        const { result } = renderHook(() => useDeleteSkill(), { wrapper: makeWrapper() })
        result.current.mutate('11111111-1111-1111-1111-111111111111')
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
    })
})

describe('useTestSkill', () => {
    it('returns SkillTestResponse', async () => {
        server.use(
            http.post('http://localhost:3000/api/skills/11111111-1111-1111-1111-111111111111/test', async ({ request }) => {
                const body = (await request.clone().json()) as { args?: unknown }
                return HttpResponse.json({ outcome: 'ok', result: body.args })
            }),
        )
        const { result } = renderHook(
            () => useTestSkill('11111111-1111-1111-1111-111111111111'),
            { wrapper: makeWrapper() },
        )
        result.current.mutate({ foo: 'bar' })
        await waitFor(() => expect(result.current.isSuccess).toBe(true))
        expect(result.current.data!.outcome).toBe('ok')
        expect(result.current.data!.result).toEqual({ foo: 'bar' })
    })
})
