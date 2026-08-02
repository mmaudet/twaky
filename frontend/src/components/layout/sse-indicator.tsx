'use client'

import { useSSEStatus } from '@/components/providers/sse-provider'
import { cn } from '@/lib/utils'

const COLORS = {
    connected: 'bg-green-500',
    reconnecting: 'bg-orange-500',
    disconnected: 'bg-gray-400',
} as const

const LABELS = {
    connected: 'Live updates connected',
    reconnecting: 'Reconnecting…',
    disconnected: 'Disconnected',
} as const

export function SSEIndicator() {
    const status = useSSEStatus()
    return (
        <div
            role="status"
            aria-label={LABELS[status]}
            title={LABELS[status]}
            className={cn(
                'h-2.5 w-2.5 rounded-full transition-colors',
                COLORS[status],
            )}
        />
    )
}
