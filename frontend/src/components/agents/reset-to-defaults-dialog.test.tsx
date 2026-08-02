import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import { ReactNode } from 'react'
import { ResetToDefaultsDialog } from './reset-to-defaults-dialog'

const server = setupServer(
    http.get('http://localhost:3000/api/agents/plume/default_prompt', () =>
        HttpResponse.json({ system_prompt: 'DEFAULT PLUME PROMPT' }),
    ),
)

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(node: ReactNode) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('ResetToDefaultsDialog', () => {
    it('opens dialog and calls onReset with default prompt after confirmation', async () => {
        const onReset = vi.fn()
        wrap(<ResetToDefaultsDialog agentId="plume" displayName="Plume" onReset={onReset} />)

        fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
        expect(screen.getByRole('alertdialog')).toBeInTheDocument()

        fireEvent.click(screen.getByRole('button', { name: /^reset$/i }))

        await waitFor(() => expect(onReset).toHaveBeenCalledWith('DEFAULT PLUME PROMPT'))
    })

    it('closes without calling onReset on Cancel', async () => {
        const onReset = vi.fn()
        wrap(<ResetToDefaultsDialog agentId="plume" displayName="Plume" onReset={onReset} />)

        fireEvent.click(screen.getByRole('button', { name: /reset to defaults/i }))
        fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

        await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
        expect(onReset).not.toHaveBeenCalled()
    })
})
