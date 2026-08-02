import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

export function useMission(id: string) {
    return useQuery({
        queryKey: ['mission', id],
        queryFn: async () => {
            const { data, error } = await api.GET('/missions/{mid}', {
                params: { path: { mid: id } },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
        enabled: !!id,
    })
}
