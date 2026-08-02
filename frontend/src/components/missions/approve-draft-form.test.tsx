import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClientProvider, QueryClient } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '@/test/mocks/server'
import { ApproveDraftForm } from './approve-draft-form'

function withQuery(children: React.ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

const artifact = {
    kind: 'approve_draft' as const,
    draft: 'Hi Bob',
    to: 'bob@x',
    subject: 'Re: Hello',
}

describe('ApproveDraftForm', () => {
    it('shows To + Subject + draft', () => {
        render(withQuery(<ApproveDraftForm missionId="m1" artifact={artifact} />))
        expect(screen.getByText('bob@x')).toBeInTheDocument()
        expect(screen.getByText('Re: Hello')).toBeInTheDocument()
        expect(screen.getByDisplayValue('Hi Bob')).toBeInTheDocument()
    })
    it('POSTs the edited draft on approve', async () => {
        const seen: Record<string, unknown>[] = []
        server.use(
            http.post('/api/missions/:mid/resume', async ({ request }) => {
                seen.push(await request.json() as Record<string, unknown>)
                return HttpResponse.json({ id: 'm1' })
            }),
        )
        render(withQuery(<ApproveDraftForm missionId="m1" artifact={artifact} />))
        const textarea = screen.getByRole('textbox')
        await userEvent.clear(textarea)
        await userEvent.type(textarea, 'Edited hello')
        await userEvent.click(screen.getByRole('button', { name: /Approve/ }))
        expect(seen[0]).toEqual({ user_response: { approved: true, draft: 'Edited hello' } })
    })
})
