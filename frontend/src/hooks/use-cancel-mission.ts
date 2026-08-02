import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

interface CancelArgs {
    id: string
    reason: string
}

export function useCancelMission() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async ({ id, reason }: CancelArgs) => {
            const { data, error } = await api.POST('/missions/{mid}/cancel', {
                params: { path: { mid: id } },
                body: { reason },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
        onSuccess: (_, { id }) => {
            qc.invalidateQueries({ queryKey: ['mission', id] })
            qc.invalidateQueries({ queryKey: ['missions'] })
        },
    })
}
