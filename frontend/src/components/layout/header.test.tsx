import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { Header } from './header'

vi.mock('next/navigation', () => ({
    useRouter: () => ({
        push: vi.fn(),
        back: vi.fn(),
        forward: vi.fn(),
        refresh: vi.fn(),
    }),
}))

function withQuery(children: React.ReactNode) {
    const qc = new QueryClient({
        defaultOptions: { queries: { retry: false } },
    })
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('Header', () => {
    it('shows the owner email once /me resolves', async () => {
        render(withQuery(<Header />))
        // MSW default handler returns alice@x
        await waitFor(() => expect(screen.getByText(/alice@x/)).toBeInTheDocument())
    })
    it('shows placeholder while loading', () => {
        render(withQuery(<Header />))
        // Before waitFor resolves, the button says "…"
        expect(screen.getByRole('button', { name: /…/ })).toBeInTheDocument()
    })
    it('shows the Agents nav link', () => {
        render(withQuery(<Header />))
        expect(screen.getByRole('link', { name: 'Agents' })).toBeInTheDocument()
    })
})
