import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { GenericResumeForm } from './generic-resume-form'

function withQuery(children: React.ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('GenericResumeForm', () => {
    it('displays the kind', () => {
        render(withQuery(<GenericResumeForm missionId="m1" kind="pick_option" />))
        expect(screen.getByText(/Action required/)).toHaveTextContent('pick_option')
    })
    it('shows JSON error on malformed input', async () => {
        render(withQuery(<GenericResumeForm missionId="m1" kind="x" />))
        const textarea = screen.getByRole('textbox')
        await userEvent.clear(textarea)
        await userEvent.type(textarea, 'not json')
        await userEvent.click(screen.getByRole('button', { name: /Submit/ }))
        expect(screen.getByText(/JSON error/)).toBeInTheDocument()
    })
})
