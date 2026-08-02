import { describe, expect, it } from 'vitest'
import { computeStateBreakdown } from './compute-state-breakdown'
import type { components } from './api-types'

type Mission = components['schemas']['Mission']

function m(state: Mission['state']): Mission {
    return {
        id: '00000000-0000-0000-0000-000000000000',
        intent_text: 'x',
        owner_email: 'a@x',
        declared_by: 'a@x',
        declared_at: '2026-08-02T10:00:00Z',
        created_at: '2026-08-02T10:00:00Z',
        updated_at: '2026-08-02T10:00:00Z',
        state,
        plan: [],
        artifacts: [],
    } as Mission
}

describe('computeStateBreakdown', () => {
    it('returns zero counters on empty input', () => {
        const { counts, totalLive, totalTerminal } = computeStateBreakdown([])
        expect(counts.done).toBe(0)
        expect(counts.awaiting_user).toBe(0)
        expect(totalLive).toBe(0)
        expect(totalTerminal).toBe(0)
    })
    it('counts states correctly', () => {
        const rows = [m('done'), m('done'), m('failed'), m('running'), m('awaiting_user')]
        const { counts, totalLive, totalTerminal } = computeStateBreakdown(rows)
        expect(counts.done).toBe(2)
        expect(counts.failed).toBe(1)
        expect(counts.running).toBe(1)
        expect(counts.awaiting_user).toBe(1)
        expect(totalLive).toBe(2)   // running + awaiting_user
        expect(totalTerminal).toBe(3)  // 2 done + 1 failed
    })
})
