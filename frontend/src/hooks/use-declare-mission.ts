import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

export function useDeclareMission() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (intentText: string) => {
            const { data, error } = await api.POST('/missions', {
                body: { intent_text: intentText },
            })
            if (error) throw new ApiError(isErrorEnvelope(error) ? error : {
                error: { code: 'unknown', message: 'Unknown API error' }
            })
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['missions'] })
        },
    })
}
