import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type MailMemorySummary = components['schemas']['MailMemorySummary']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useMailMemories(opts?: { scope?: string; limit?: number }) {
    return useQuery({
        queryKey: ['mail-memories', opts],
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/memories', {
                params: {
                    query: {
                        scope: opts?.scope,
                        limit: opts?.limit,
                    },
                },
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

export function useForgetMailMemory() {
    const queryClient = useQueryClient()
    return useMutation({
        mutationFn: async (memoryId: string) => {
            const { error } = await api.DELETE('/mail-sentinel/memories/{memory_id}', {
                params: { path: { memory_id: memoryId } },
            })
            if (error) throw toApiError(error)
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['mail-memories'] })
        },
    })
}
