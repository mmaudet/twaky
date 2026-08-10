import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type AuthStatus = components['schemas']['AuthStatus']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

const AUTH_QUERY_KEY = ['mail-sentinel-auth'] as const

export function useMailSentinelAuth() {
    return useQuery({
        queryKey: AUTH_QUERY_KEY,
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/auth')
            if (error) throw toApiError(error)
            return data
        },
        staleTime: 30_000,
    })
}

export function useForceRefresh() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async () => {
            const { data, error } = await api.POST('/mail-sentinel/auth/refresh')
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: AUTH_QUERY_KEY })
        },
    })
}

export function useDisconnect() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async () => {
            const { error } = await api.DELETE('/mail-sentinel/auth')
            if (error) throw toApiError(error)
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: AUTH_QUERY_KEY })
        },
    })
}
