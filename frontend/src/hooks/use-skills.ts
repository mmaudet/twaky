import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type Skill = components['schemas']['Skill']
export type SkillSummary = components['schemas']['SkillSummary']
export type SkillCreate = components['schemas']['SkillCreate']
export type SkillUpdate = components['schemas']['SkillUpdate']
export type SkillTestRequest = components['schemas']['SkillTestRequest']
export type SkillTestResponse = components['schemas']['SkillTestResponse']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useSkills() {
    return useQuery({
        queryKey: ['skills'],
        queryFn: async () => {
            const { data, error } = await api.GET('/skills')
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useSkill(id: string | undefined) {
    return useQuery({
        queryKey: ['skill', id],
        enabled: !!id && id !== 'new',
        queryFn: async () => {
            const { data, error } = await api.GET('/skills/{skill_id}', {
                params: { path: { skill_id: id! } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useCreateSkill() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (body: SkillCreate) => {
            const { data, error } = await api.POST('/skills', { body })
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['skills'] })
        },
    })
}

export function useUpdateSkill(id: string) {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (body: SkillUpdate) => {
            const { data, error } = await api.PATCH('/skills/{skill_id}', {
                params: { path: { skill_id: id } },
                body,
            })
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['skills'] })
            qc.invalidateQueries({ queryKey: ['skill', id] })
        },
    })
}

export function useDeleteSkill() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (id: string) => {
            const { error } = await api.DELETE('/skills/{skill_id}', {
                params: { path: { skill_id: id } },
            })
            if (error) throw toApiError(error)
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['skills'] })
        },
    })
}

export function useTestSkill(id: string) {
    return useMutation({
        mutationFn: async (args: Record<string, unknown>): Promise<SkillTestResponse> => {
            const { data, error } = await api.POST('/skills/{skill_id}/test', {
                params: { path: { skill_id: id } },
                body: { args },
            })
            if (error) throw toApiError(error)
            return data!
        },
    })
}
