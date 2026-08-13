import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type ObservationSummary = components['schemas']['ObservationSummary']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useMailObservations(opts?: { limit?: number }) {
    return useQuery({
        queryKey: ['mail-observations', opts],
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/observations', {
                params: { query: { limit: opts?.limit } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function usePatchMailMemory() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async ({
            memory_id,
            persist,
        }: {
            memory_id: string
            persist: boolean
        }) => {
            const { data, error } = await api.PATCH(
                '/mail-sentinel/memories/{memory_id}',
                {
                    params: { path: { memory_id } },
                    body: { persist },
                },
            )
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['mail-memories'] })
        },
    })
}

/** Forget = reset to 7-day TTL (PATCH persist=false). */
export function useForgetMailMemory() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (memory_id: string) => {
            const { data, error } = await api.PATCH(
                '/mail-sentinel/memories/{memory_id}',
                {
                    params: { path: { memory_id } },
                    body: { persist: false },
                },
            )
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['mail-memories'] })
        },
    })
}
