import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type MissionState = components['schemas']['MissionState']

export function useMissions(state?: MissionState) {
    return useQuery({
        queryKey: ['missions', { state }],
        queryFn: async () => {
            const { data, error } = await api.GET('/missions', {
                params: { query: state ? { state } : {} },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
    })
}
