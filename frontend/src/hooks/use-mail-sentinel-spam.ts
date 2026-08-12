import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type SpamDecision = components['schemas']['SpamDecision']
export type SpamStats = components['schemas']['SpamStats']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export interface SpamDecisionsFilter {
    bucket?: string
    limit?: number
    before?: string
    withProvenance?: boolean
}

export function useSpamDecisions(filters: SpamDecisionsFilter = {}) {
    const { bucket, limit = 50, before, withProvenance = false } = filters
    return useQuery({
        queryKey: [
            'mail-spam-decisions',
            { bucket, before, withProvenance },
        ] as const,
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/spam', {
                params: {
                    query: {
                        bucket,
                        limit,
                        before,
                        with_provenance: withProvenance,
                    },
                },
            })
            if (error) throw toApiError(error)
            return data
        },
        staleTime: 30_000,
    })
}

export function useSpamStats(days = 30) {
    return useQuery({
        queryKey: ['mail-spam-stats', { days }] as const,
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/spam/stats', {
                params: {
                    query: { days },
                },
            })
            if (error) throw toApiError(error)
            return data
        },
        staleTime: 60_000,
    })
}

export function useRestoreSpam() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (decisionId: string) => {
            const { data, error } = await api.POST(
                '/mail-sentinel/spam/{decision_id}/restore',
                {
                    params: { path: { decision_id: decisionId } },
                },
            )
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['mail-spam-decisions'] })
            qc.invalidateQueries({ queryKey: ['mail-spam-stats'] })
        },
    })
}
