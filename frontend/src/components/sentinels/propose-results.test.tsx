/**
 * Tests for ProposeResults component.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ProposeResults } from './propose-results'
import type { components } from '@/lib/api-types'

type ProposeResponse = components['schemas']['MailRuleProposeResponse']

function makeResponse(overrides: Partial<ProposeResponse> = {}): ProposeResponse {
    return {
        valid: true,
        matched_count: 3,
        would_shadow_count: 1,
        would_shadow: ['archive-all'],
        matched_examples: [
            {
                decision_id: 'dec-1',
                sender: 'spam@example.com',
                subject: 'Buy now!',
                current_bucket: 'spam',
                would_shadow_by: null,
            },
            {
                decision_id: 'dec-2',
                sender: 'news@mail.com',
                subject: 'Weekly digest',
                current_bucket: 'newsletter',
                would_shadow_by: 'archive-all',
            },
        ],
        simulation_partial: false,
        simulation_partial_reason: null,
        ...overrides,
    }
}

describe('ProposeResults', () => {
    it('renders summary counts correctly', () => {
        render(
            <ProposeResults
                data={makeResponse()}
                reviewed={false}
                onReviewedChange={vi.fn()}
            />,
        )

        const summary = screen.getByText(/3 matches/)
        expect(summary).toBeTruthy()
        expect(summary.textContent).toContain('1 shadowed by')
        expect(summary.textContent).toContain('archive-all')
    })

    it('renders shadow warning icon on rows with would_shadow_by', () => {
        render(
            <ProposeResults
                data={makeResponse()}
                reviewed={false}
                onReviewedChange={vi.fn()}
            />,
        )

        // The row with would_shadow_by='archive-all' should show warning
        const warningImgs = screen.getAllByRole('img', { name: 'warning' })
        expect(warningImgs.length).toBe(1)

        // The title should reference the shadow rule
        const shadowCell = screen.getByTitle('would be pre-empted by archive-all')
        expect(shadowCell).toBeTruthy()
    })

    it('renders Simulation partial alert when flag true', () => {
        render(
            <ProposeResults
                data={makeResponse({
                    simulation_partial: true,
                    simulation_partial_reason: 'Only 100 of 2000 rows scanned',
                })}
                reviewed={false}
                onReviewedChange={vi.fn()}
            />,
        )

        const alert = screen.getByRole('alert')
        expect(alert).toBeTruthy()
        expect(alert.textContent).toContain('Only 100 of 2000 rows scanned')
    })

    it('does not render Simulation partial alert when flag false', () => {
        render(
            <ProposeResults
                data={makeResponse({ simulation_partial: false, simulation_partial_reason: null })}
                reviewed={false}
                onReviewedChange={vi.fn()}
            />,
        )

        expect(screen.queryByRole('alert')).toBeNull()
    })

    it('renders "No historical decision matched" when list empty', () => {
        render(
            <ProposeResults
                data={makeResponse({ matched_examples: [] })}
                reviewed={false}
                onReviewedChange={vi.fn()}
            />,
        )

        expect(
            screen.getByText(/no historical decision matched this rule/i),
        ).toBeTruthy()
    })

    it('shows "none" in summary when would_shadow list is empty', () => {
        render(
            <ProposeResults
                data={makeResponse({ would_shadow: [], would_shadow_count: 0 })}
                reviewed={false}
                onReviewedChange={vi.fn()}
            />,
        )

        const summary = screen.getByText(/0 shadowed by none/)
        expect(summary).toBeTruthy()
    })

    it('checkbox toggle calls onReviewedChange', () => {
        const onReviewedChange = vi.fn()
        render(
            <ProposeResults
                data={makeResponse()}
                reviewed={false}
                onReviewedChange={onReviewedChange}
            />,
        )

        const checkbox = screen.getByRole('checkbox', {
            name: /i have reviewed the matches/i,
        })
        fireEvent.click(checkbox)

        expect(onReviewedChange).toHaveBeenCalledWith(true)
    })

    it('checkbox reflects reviewed=true', () => {
        render(
            <ProposeResults
                data={makeResponse()}
                reviewed={true}
                onReviewedChange={vi.fn()}
            />,
        )

        const checkbox = screen.getByRole('checkbox', {
            name: /i have reviewed the matches/i,
        })
        expect(checkbox.getAttribute('data-state')).toBe('checked')
    })
})
