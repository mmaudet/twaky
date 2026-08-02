import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MissionList } from './mission-list'
import type { components } from '@/lib/api-types'

type Mission = components['schemas']['Mission']

function m(id: string, intent: string, state: Mission['state'] = 'declared'): Mission {
    return {
        id, intent_text: intent, owner_email: 'a@x', declared_by: 'a@x',
        declared_at: '2026-08-02T09:00:00Z', state, plan: [], artifacts: [],
        created_at: '2026-08-02T09:00:00Z', updated_at: '2026-08-02T09:00:00Z',
    } as Mission
}

describe('MissionList', () => {
    it('shows empty state when list is empty', () => {
        render(<MissionList missions={[]} />)
        expect(screen.getByText(/No missions yet/)).toBeInTheDocument()
    })
    it('renders one row per mission', () => {
        render(<MissionList missions={[m('a', 'first'), m('b', 'second')]} />)
        expect(screen.getByText('first')).toBeInTheDocument()
        expect(screen.getByText('second')).toBeInTheDocument()
    })
    it('highlights awaiting_user rows', () => {
        const { container } = render(
            <MissionList missions={[m('a', 'attn', 'awaiting_user')]} />,
        )
        const rows = container.querySelectorAll('tbody tr')
        expect(rows[0].className).toContain('yellow')
    })
})
