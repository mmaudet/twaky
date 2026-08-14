import { useQuery } from '@tanstack/react-query'
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
