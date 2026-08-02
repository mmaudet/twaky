import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

export function useMe() {
    return useQuery({
        queryKey: ['me'],
        queryFn: async () => {
            const { data, error } = await api.GET('/me')
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
    })
}
