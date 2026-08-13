import { describe, test, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryCard } from './MemoryCard'

const baseMem = {
    id: 'mem-1',
    kind: 'preference',
    scope: 'sender',
    scope_value: 'alex@x.com',
    content: 'Use Bonjour',
    source: 'auto_diff',
    confidence: 0.85,
    mission_id: null,
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 5 * 86400 * 1000).toISOString(),
}

describe('MemoryCard', () => {
    test('shows auto badge for auto_diff source', () => {
        render(<MemoryCard memory={baseMem} onForget={() => {}} onPersist={() => {}} />)
        expect(screen.getByText(/auto_diff/i)).toBeInTheDocument()
        expect(screen.getByText(/alex@x.com/)).toBeInTheDocument()
    })

    test('shows manual badge for manual source', () => {
        render(
            <MemoryCard
                memory={{ ...baseMem, source: 'manual' }}
                onForget={() => {}}
                onPersist={() => {}}
            />,
        )
        expect(screen.getByText(/manual/i)).toBeInTheDocument()
    })

    test('Forget button calls onForget with memory id', () => {
        const onForget = vi.fn()
        render(<MemoryCard memory={baseMem} onForget={onForget} onPersist={() => {}} />)
        fireEvent.click(screen.getByText(/Forget/))
        expect(onForget).toHaveBeenCalledWith('mem-1')
    })

    test('Keep permanent button visible when expires_at is set', () => {
        render(<MemoryCard memory={baseMem} onForget={() => {}} onPersist={() => {}} />)
        expect(screen.getByText(/Keep permanent/i)).toBeInTheDocument()
    })

    test('No Keep permanent button when already permanent', () => {
        render(
            <MemoryCard
                memory={{ ...baseMem, expires_at: null }}
                onForget={() => {}}
                onPersist={() => {}}
            />,
        )
        expect(screen.queryByText(/Keep permanent/i)).toBeNull()
    })
})
