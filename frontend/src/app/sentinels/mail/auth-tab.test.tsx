import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Next.js navigation mock ───────────────────────────────────────────────────

vi.mock('next/navigation', () => ({
    useSearchParams: () => new URLSearchParams(),
}))

// ── Hook mocks ────────────────────────────────────────────────────────────────

const mockRefreshMutate = vi.fn()
const mockDisconnectMutate = vi.fn()

vi.mock('@/hooks/use-mail-sentinel-auth', () => ({
    useMailSentinelAuth: vi.fn(),
    useForceRefresh: vi.fn(),
    useDisconnect: vi.fn(),
}))

import {
    useMailSentinelAuth,
    useForceRefresh,
    useDisconnect,
} from '@/hooks/use-mail-sentinel-auth'
import { AuthTab } from './auth-tab'

const mockUseMailSentinelAuth = vi.mocked(useMailSentinelAuth)
const mockUseForceRefresh = vi.mocked(useForceRefresh)
const mockUseDisconnect = vi.mocked(useDisconnect)

const CONNECTED_STATUS = {
    connected: true as const,
    provider: 'fastmail',
    account_email: 'me@example.com',
    account_name: 'Me Example',
    session_url: 'https://jmap.fastmail.com',
    access_token_expires_at: new Date(Date.now() + 30 * 60_000).toISOString(),
    last_refresh_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    last_refresh_error: null,
}

function setupDefaultMutations() {
    mockUseForceRefresh.mockReturnValue({
        mutate: mockRefreshMutate,
        isPending: false,
    } as unknown as ReturnType<typeof useForceRefresh>)
    mockUseDisconnect.mockReturnValue({
        mutate: mockDisconnectMutate,
        isPending: false,
    } as unknown as ReturnType<typeof useDisconnect>)
}

beforeEach(() => {
    vi.clearAllMocks()
    setupDefaultMutations()
})

describe('AuthTab', () => {
    it('test_not_connected_renders_connect_button', () => {
        mockUseMailSentinelAuth.mockReturnValue({
            data: { connected: false, provider: null, account_email: null, account_name: null, session_url: null, access_token_expires_at: null, last_refresh_at: null, last_refresh_error: null },
            isLoading: false,
            error: null,
        } as ReturnType<typeof useMailSentinelAuth>)

        render(<AuthTab />)

        expect(screen.getByRole('button', { name: /connect jmap account/i })).toBeTruthy()
    })

    it('test_connected_renders_status_and_actions', () => {
        mockUseMailSentinelAuth.mockReturnValue({
            data: CONNECTED_STATUS,
            isLoading: false,
            error: null,
        } as ReturnType<typeof useMailSentinelAuth>)

        render(<AuthTab />)

        expect(screen.getByText('me@example.com')).toBeTruthy()
        expect(screen.getByRole('button', { name: /force refresh/i })).toBeTruthy()
        expect(screen.getByRole('button', { name: /reconnect/i })).toBeTruthy()
        expect(screen.getByRole('button', { name: /disconnect/i })).toBeTruthy()
    })

    it('test_disconnect_opens_confirmation_dialog', async () => {
        mockUseMailSentinelAuth.mockReturnValue({
            data: CONNECTED_STATUS,
            isLoading: false,
            error: null,
        } as ReturnType<typeof useMailSentinelAuth>)

        const user = userEvent.setup()
        render(<AuthTab />)

        await user.click(screen.getByRole('button', { name: /disconnect/i }))

        await waitFor(() => {
            expect(screen.getByRole('alertdialog')).toBeTruthy()
        })
        expect(screen.getByText(/disconnect jmap account/i)).toBeTruthy()
    })

    it('test_disconnect_confirm_calls_mutation', async () => {
        mockUseMailSentinelAuth.mockReturnValue({
            data: CONNECTED_STATUS,
            isLoading: false,
            error: null,
        } as ReturnType<typeof useMailSentinelAuth>)

        const user = userEvent.setup()
        render(<AuthTab />)

        // Open the dialog
        await user.click(screen.getByRole('button', { name: /disconnect/i }))
        await waitFor(() => {
            expect(screen.getByRole('alertdialog')).toBeTruthy()
        })

        // Confirm disconnect — there are two "Disconnect" buttons (trigger + action),
        // pick the one inside the dialog (AlertDialogAction)
        const confirmBtn = screen.getAllByRole('button', { name: /disconnect/i }).find(
            (btn) => btn.closest('[role="alertdialog"]'),
        )
        expect(confirmBtn).toBeTruthy()
        await user.click(confirmBtn!)

        expect(mockDisconnectMutate).toHaveBeenCalledTimes(1)
    })

    it('test_force_refresh_calls_mutation', async () => {
        mockUseMailSentinelAuth.mockReturnValue({
            data: CONNECTED_STATUS,
            isLoading: false,
            error: null,
        } as ReturnType<typeof useMailSentinelAuth>)

        const user = userEvent.setup()
        render(<AuthTab />)

        await user.click(screen.getByRole('button', { name: /force refresh/i }))

        expect(mockRefreshMutate).toHaveBeenCalledTimes(1)
    })
})
