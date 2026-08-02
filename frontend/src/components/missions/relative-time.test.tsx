import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RelativeTime } from './relative-time'

describe('RelativeTime', () => {
    it('renders "s ago" for recent timestamp', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        render(<RelativeTime timestamp="2026-08-02T09:59:30Z" />)
        expect(screen.getByText(/30s ago/)).toBeInTheDocument()
        vi.useRealTimers()
    })
    it('renders "m ago" for minutes-old timestamp', () => {
        vi.useFakeTimers()
        vi.setSystemTime(new Date('2026-08-02T10:00:00Z'))
        render(<RelativeTime timestamp="2026-08-02T09:55:00Z" />)
        expect(screen.getByText(/5m ago/)).toBeInTheDocument()
        vi.useRealTimers()
    })
    it('renders em-dash on invalid input', () => {
        render(<RelativeTime timestamp="not-a-date" />)
        expect(screen.getByText('—')).toBeInTheDocument()
    })
})
