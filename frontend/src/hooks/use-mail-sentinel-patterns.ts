import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type LearnedPatternSummary = components['schemas']['LearnedPatternSummary']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useMailPatterns(opts?: { active_only?: boolean }) {
    return useQuery({
        queryKey: ['mail-patterns', opts],
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/learned-patterns', {
                params: { query: { active_only: opts?.active_only } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useForgetMailPattern() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async ({
            sender_email,
            rule_name,
        }: {
            sender_email: string
            rule_name: string
        }) => {
            const { error } = await api.DELETE(
                '/mail-sentinel/learned-patterns/{sender_email}/{rule_name}',
                {
                    params: { path: { sender_email, rule_name } },
                },
            )
            if (error) throw toApiError(error)
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['mail-patterns'] })
        },
    })
}
