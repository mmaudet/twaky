import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { ApiError, isErrorEnvelope } from '@/lib/api-error'
import type { components } from '@/lib/api-types'

export type MailRuleSummary = components['schemas']['MailRuleSummary']
export type MailRuleDetail = components['schemas']['MailRuleDetail']
export type MailRuleCreate = components['schemas']['MailRuleCreate']
export type MailRulePatch = components['schemas']['MailRulePatch']

function toApiError(error: unknown): ApiError {
    return new ApiError(
        isErrorEnvelope(error)
            ? error
            : { error: { code: 'unknown', message: 'Unknown API error' } },
    )
}

export function useMailRules(opts?: { enabled?: boolean }) {
    return useQuery({
        queryKey: ['mail-rules', opts],
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/rules', {
                params: { query: { enabled: opts?.enabled } },
            })
            if (error) throw toApiError(error)
            return data
        },
    })
}

export function useMailRule(id: string) {
    return useQuery({
        queryKey: ['mail-rules', id],
        queryFn: async () => {
            const { data, error } = await api.GET('/mail-sentinel/rules/{rule_id}', {
                params: { path: { rule_id: id } },
            })
            if (error) throw toApiError(error)
            return data
        },
        enabled: !!id && id !== 'new',
    })
}

export function useCreateMailRule() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (body: MailRuleCreate) => {
            const { data, error } = await api.POST('/mail-sentinel/rules', { body })
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['mail-rules'] })
        },
    })
}

export function usePatchMailRule(id: string) {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (body: MailRulePatch) => {
            const { data, error } = await api.PATCH('/mail-sentinel/rules/{rule_id}', {
                params: { path: { rule_id: id } },
                body,
            })
            if (error) throw toApiError(error)
            return data
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['mail-rules'] })
            qc.invalidateQueries({ queryKey: ['mail-rules', id] })
        },
    })
}

export function useDeleteMailRule() {
    const qc = useQueryClient()
    return useMutation({
        mutationFn: async (id: string) => {
            const { error } = await api.DELETE('/mail-sentinel/rules/{rule_id}', {
                params: { path: { rule_id: id } },
            })
            if (error) throw toApiError(error)
        },
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['mail-rules'] })
        },
    })
}
