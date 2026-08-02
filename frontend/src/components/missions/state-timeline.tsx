import type { components } from '@/lib/api-types'
import { cn } from '@/lib/utils'

type MissionState = components['schemas']['MissionState']

/**
 * Renders a horizontal timeline of states.
 * Reached states are filled; unreached are outlined.
 */
export function StateTimeline({ currentState }: { currentState: MissionState }) {
    const order: MissionState[] = [
        'declared', 'planning', 'running',
        'awaiting_user', 'done',
    ]
    const currentIdx = order.indexOf(currentState)

    return (
        <ol className="flex items-center gap-2 text-xs">
            {order.map((s, i) => {
                const reached = i <= currentIdx && currentIdx >= 0
                return (
                    <li key={s} className="flex items-center gap-2">
                        <div className={cn(
                            'h-3 w-3 rounded-full border',
                            reached ? 'bg-primary border-primary' : 'bg-background border-muted-foreground/50',
                        )} />
                        <span className={reached ? 'font-medium' : 'text-muted-foreground'}>
                            {s}
                        </span>
                        {i < order.length - 1 && <span className="text-muted-foreground">─</span>}
                    </li>
                )
            })}
        </ol>
    )
}
