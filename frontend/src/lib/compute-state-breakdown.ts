import type { components } from './api-types'

type Mission = components['schemas']['Mission']
type MissionState = components['schemas']['MissionState']

const ALL_STATES: MissionState[] = [
    'declared', 'planning', 'running', 'awaiting_user',
    'done', 'failed', 'cancelled',
]

const TERMINAL: Set<MissionState> = new Set(['done', 'failed', 'cancelled'])

export interface StateBreakdown {
    counts: Record<MissionState, number>
    totalLive: number
    totalTerminal: number
}

export function computeStateBreakdown(missions: Mission[]): StateBreakdown {
    const counts = Object.fromEntries(
        ALL_STATES.map((s) => [s, 0]),
    ) as Record<MissionState, number>

    for (const m of missions) {
        counts[m.state] = (counts[m.state] ?? 0) + 1
    }

    const totalLive = missions.filter((m) => !TERMINAL.has(m.state)).length
    const totalTerminal = missions.length - totalLive
    return { counts, totalLive, totalTerminal }
}
