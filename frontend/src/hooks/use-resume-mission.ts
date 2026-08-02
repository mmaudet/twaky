import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'

interface ResumeArgs {
    id: string
    userResponse: Record<string, unknown>
}

export function useResumeMission() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async ({ id, userResponse }: ResumeArgs) => {
            const { data, error } = await api.POST('/missions/{mid}/resume', {
                params: { path: { mid: id } },
                body: { user_response: userResponse },
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
