'use client'

import type { components } from '@/lib/api-types'

type MailMemorySummary = components['schemas']['MailMemorySummary']

type Props = {
    memory: MailMemorySummary
    onForget: (id: string) => void
    onPersist: (id: string, persist: boolean) => void
}

function sourceBadge(source: string): { emoji: string; label: string } {
    if (source === 'manual') return { emoji: '✍️', label: 'manual' }
    return { emoji: '🤖', label: source }
}

function relativeAge(iso: string): string {
    const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
    if (days === 0) return 'today'
    if (days === 1) return '1 day ago'
    return `${days} days ago`
}

export function MemoryCard({ memory, onForget, onPersist }: Props) {
    const badge = sourceBadge(memory.source)
    const isPermanent = memory.expires_at === null || memory.expires_at === undefined
    return (
        <div className="border rounded p-3 mb-2 bg-white dark:bg-neutral-900">
            <div className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400 mb-1">
                <span>{badge.emoji}</span>
                <span>{badge.label}</span>
                <span>·</span>
                <span>{memory.scope}</span>
                {memory.scope !== 'global' && memory.scope_value && (
                    <>
                        <span>·</span>
                        <span className="font-mono">{memory.scope_value}</span>
                    </>
                )}
                {memory.confidence !== null && memory.confidence !== undefined && (
                    <>
                        <span>·</span>
                        <span>conf {memory.confidence.toFixed(2)}</span>
                    </>
                )}
            </div>
            <div className="text-sm mb-2">{memory.content}</div>
            <div className="text-xs text-neutral-500 mb-2">
                Learned {relativeAge(memory.created_at)}
                {isPermanent
                    ? ' · no expiry'
                    : ` · expires ${relativeAge(memory.expires_at!)}`}
            </div>
            <div className="flex gap-2">
                <button
                    onClick={() => onForget(memory.id)}
                    className="text-xs px-2 py-1 border rounded hover:bg-neutral-100 dark:hover:bg-neutral-800"
                >
                    Forget
                </button>
                {!isPermanent && (
                    <button
                        onClick={() => onPersist(memory.id, true)}
                        className="text-xs px-2 py-1 border rounded hover:bg-neutral-100 dark:hover:bg-neutral-800"
                    >
                        Keep permanent
                    </button>
                )}
            </div>
        </div>
    )
}
