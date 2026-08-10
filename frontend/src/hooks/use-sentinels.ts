import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type SentinelSummary = components['schemas']['SentinelSummary']
export type SentinelDetail = components['schemas']['SentinelDetail']
export type SentinelPatch = components['schemas']['SentinelPatch']
export type SentinelRunSummary = components['schemas']['SentinelRunSummary']
export type SentinelRunDetail = components['schemas']['SentinelRunDetail']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useSentinels() {
    return useQuery({
        queryKey: ['sentinels'],
        queryFn: async () => {
            const { data, error } = await api.GET('/sentinels')
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useSentinel(name: string) {
    return useQuery({
        queryKey: ['sentinels', name],
        queryFn: async () => {
            const { data, error } = await api.GET('/sentinels/{name}', {
                params: { path: { name } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function usePatchSentinel(name: string) {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (body: SentinelPatch) => {
            const { data, error } = await api.PATCH('/sentinels/{name}', {
                params: { path: { name } },
                body,
            })
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['sentinels'] })
            qc.invalidateQueries({ queryKey: ['sentinels', name] })
        },
    })
}

export function useSentinelRuns(
    name: string,
    opts?: { limit?: number; before?: string },
) {
    return useQuery({
        queryKey: ['sentinels', name, 'runs', opts],
        queryFn: async () => {
            const { data, error } = await api.GET('/sentinels/{name}/runs', {
                params: {
                    path: { name },
                    query: { limit: opts?.limit, before: opts?.before },
                },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useSentinelRunDetail(runId: string) {
    return useQuery({
        queryKey: ['sentinel-run', runId],
        queryFn: async () => {
            const { data, error } = await api.GET('/sentinels/runs/{run_id}', {
                params: { path: { run_id: runId } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}
