import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type Agent = components['schemas']['Agent']
export type AgentSummary = components['schemas']['AgentSummary']
export type AgentUpdate = components['schemas']['AgentUpdate']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useAgents() {
    return useQuery({
        queryKey: ['agents'],
        queryFn: async () => {
            const { data, error } = await api.GET('/agents')
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useAgent(id: string) {
    return useQuery({
        queryKey: ['agent', id],
        queryFn: async () => {
            const { data, error } = await api.GET('/agents/{agent_id}', {
                params: { path: { agent_id: id } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useDefaultPrompt(id: string, options?: { enabled?: boolean }) {
    return useQuery({
        queryKey: ['agent-default', id],
        enabled: options?.enabled ?? false,
        queryFn: async () => {
            const { data, error } = await api.GET('/agents/{agent_id}/default_prompt', {
                params: { path: { agent_id: id } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useUpdateAgent(id: string) {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (patch: AgentUpdate) => {
            const { data, error } = await api.PATCH('/agents/{agent_id}', {
                params: { path: { agent_id: id } },
                body: patch,
            })
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['agent', id] })
            qc.invalidateQueries({ queryKey: ['agents'] })
        },
    })
}
