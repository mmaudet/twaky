import { useQuery } from '@tanstack/react-query'
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
