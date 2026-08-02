import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StateBadge } from './state-badge'

describe('StateBadge', () => {
    it('renders the state text', () => {
        render(<StateBadge state="running" />)
        expect(screen.getByText('running')).toBeInTheDocument()
    })
    it.each(['declared', 'planning', 'running', 'awaiting_user', 'done', 'failed', 'cancelled'] as const)(
        'renders %s without crashing',
        (state) => {
            render(<StateBadge state={state} />)
            expect(screen.getByText(state)).toBeInTheDocument()
        },
    )
})
