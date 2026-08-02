import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type MissionState = components['schemas']['MissionState']

export function useMissions(state?: MissionState, includeTerminal = false) {
    return useQuery({
        queryKey: ['missions', { state, includeTerminal }],
        queryFn: async () => {
            const { data, error } = await api.GET('/missions', {
                params: {
                    query: {
                        ...(state ? { state } : {}),
                        ...(includeTerminal ? { include_terminal: true } : {}),
                    },
                },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
    })
}
