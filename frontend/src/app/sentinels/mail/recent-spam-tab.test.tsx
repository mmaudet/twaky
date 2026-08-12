import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Hook mocks ────────────────────────────────────────────────────────────────

const mockPatchMutate = vi.fn()
const mockRestoreMutate = vi.fn()

vi.mock('@/hooks/use-sentinels', () => ({
    useSentinel: vi.fn(),
    usePatchSentinel: vi.fn(),
}))

vi.mock('@/hooks/use-mail-sentinel-spam', () => ({
    useSpamDecisions: vi.fn(),
    useSpamStats: vi.fn(),
    useRestoreSpam: vi.fn(),
}))

import {
    useSentinel,
    usePatchSentinel,
} from '@/hooks/use-sentinels'
import {
    useSpamDecisions,
    useSpamStats,
    useRestoreSpam,
} from '@/hooks/use-mail-sentinel-spam'
import { RecentSpamTab } from './recent-spam-tab'

const mockUseSentinel = vi.mocked(useSentinel)
const mockUsePatchSentinel = vi.mocked(usePatchSentinel)
const mockUseSpamDecisions = vi.mocked(useSpamDecisions)
const mockUseSpamStats = vi.mocked(useSpamStats)
const mockUseRestoreSpam = vi.mocked(useRestoreSpam)

// ── Fixtures ──────────────────────────────────────────────────────────────────

const SENTINEL_OFF = {
    name: 'mail',
    display_name: 'Mail Sentinel',
    description: '',
    enabled: true,
    version: '1',
    config_schema: {},
    config_values: { spam_filter_enabled: false },
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
}

const SENTINEL_ON = {
    ...SENTINEL_OFF,
    config_values: { spam_filter_enabled: true, spam_purge_active_days: 30, spam_purge_restored_days: 90 },
}

const SAMPLE_STATS = {
    spam: 3,
    newsletter: 2,
    phishing_alert: 1,
    restored: 1,
    total_processed: 6,
}

const SAMPLE_DECISIONS = [
    {
        id: 'aaaaaaaa-0000-0000-0000-000000000001',
        email_id: 'email-1',
        thread_id: 'thread-1',
        sender_email: 'spammer@evil.com',
        subject: 'Win a prize!',
        received_at: new Date(Date.now() - 60_000).toISOString(),
        bucket: 'spam',
        signal_source: 'rspamd_junk_keyword',
        score: null,
        reason: null,
        restored_at: null,
        restored_by: null,
        decided_at: new Date(Date.now() - 30_000).toISOString(),
    },
    {
        id: 'aaaaaaaa-0000-0000-0000-000000000002',
        email_id: 'email-2',
        thread_id: null,
        sender_email: 'newsletter@marketing.com',
        subject: 'Weekly deals inside!',
        received_at: new Date(Date.now() - 120_000).toISOString(),
        bucket: 'newsletter',
        signal_source: 'heuristic_newsletter',
        score: null,
        reason: null,
        restored_at: null,
        restored_by: null,
        decided_at: new Date(Date.now() - 90_000).toISOString(),
    },
]

function setupDefaultHooks() {
    mockUsePatchSentinel.mockReturnValue({
        mutate: mockPatchMutate,
        isPending: false,
    } as unknown as ReturnType<typeof usePatchSentinel>)

    mockUseSpamStats.mockReturnValue({
        data: SAMPLE_STATS,
        isLoading: false,
        error: null,
    } as unknown as ReturnType<typeof useSpamStats>)

    mockUseRestoreSpam.mockReturnValue({
        mutate: mockRestoreMutate,
        isPending: false,
    } as unknown as ReturnType<typeof useRestoreSpam>)
}

beforeEach(() => {
    vi.clearAllMocks()
    setupDefaultHooks()
})

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('RecentSpamTab', () => {
    it('test_off_state_renders_toggle_and_disabled_message', () => {
        mockUseSentinel.mockReturnValue({
            data: SENTINEL_OFF,
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSentinel>)

        mockUseSpamDecisions.mockReturnValue({
            data: [],
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSpamDecisions>)

        render(<RecentSpamTab />)

        // Toggle should be present and unchecked
        const toggle = screen.getByRole('switch', { name: /spam filter/i })
        expect(toggle).toBeTruthy()
        expect((toggle as HTMLInputElement).getAttribute('data-state')).toBe('unchecked')

        // Should show "off" empty state
        expect(screen.getByText(/spam filter is off/i)).toBeTruthy()
    })

    it('test_on_state_renders_stats_and_empty_message_when_no_rows', () => {
        mockUseSentinel.mockReturnValue({
            data: SENTINEL_ON,
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSentinel>)

        mockUseSpamDecisions.mockReturnValue({
            data: [],
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSpamDecisions>)

        render(<RecentSpamTab />)

        // Toggle should be checked
        const toggle = screen.getByRole('switch', { name: /spam filter/i })
        expect((toggle as HTMLInputElement).getAttribute('data-state')).toBe('checked')

        // Should show stats line with correct breakdown:
        // spam(3) + phishing_alert(1) = 4 archived, newsletter(2) labeled, restored(1) restored
        const statsLine = screen.getByText(/last 30 days/i)
        expect(statsLine).toBeTruthy()
        expect(statsLine.textContent).toContain('4 archived')
        expect(statsLine.textContent).toContain('2 labeled')
        expect(statsLine.textContent).toContain('1 restored')
        // newsletter must NOT be counted in "archived"
        expect(statsLine.textContent).not.toContain('6 archived')

        // Should show "on, no decisions" empty state
        expect(screen.getByText(/spam filter is on, no decisions yet/i)).toBeTruthy()
    })

    it('test_on_state_renders_table_rows', () => {
        mockUseSentinel.mockReturnValue({
            data: SENTINEL_ON,
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSentinel>)

        mockUseSpamDecisions.mockReturnValue({
            data: SAMPLE_DECISIONS,
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSpamDecisions>)

        render(<RecentSpamTab />)

        // Both email rows visible
        expect(screen.getByText('spammer@evil.com')).toBeTruthy()
        expect(screen.getByText('newsletter@marketing.com')).toBeTruthy()
        expect(screen.getByText('Win a prize!')).toBeTruthy()
        expect(screen.getByText('Weekly deals inside!')).toBeTruthy()

        // Signal sources visible
        expect(screen.getByText('rspamd_junk_keyword')).toBeTruthy()
        expect(screen.getByText('heuristic_newsletter')).toBeTruthy()

        // Restore buttons present
        const restoreButtons = screen.getAllByRole('button', { name: /restore/i })
        expect(restoreButtons.length).toBeGreaterThanOrEqual(2)
    })

    it('test_restore_click_opens_dialog', async () => {
        mockUseSentinel.mockReturnValue({
            data: SENTINEL_ON,
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSentinel>)

        mockUseSpamDecisions.mockReturnValue({
            data: [SAMPLE_DECISIONS[0]],
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSpamDecisions>)

        const user = userEvent.setup()
        render(<RecentSpamTab />)

        // Click the Restore button to open dialog
        const restoreBtn = screen.getByRole('button', { name: /restore/i })
        await user.click(restoreBtn)

        await waitFor(() => {
            expect(screen.getByRole('alertdialog')).toBeTruthy()
        })
        expect(screen.getByText(/restore this email\?/i)).toBeTruthy()
    })

    it('test_restore_confirm_calls_mutation', async () => {
        mockUseSentinel.mockReturnValue({
            data: SENTINEL_ON,
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSentinel>)

        mockUseSpamDecisions.mockReturnValue({
            data: [SAMPLE_DECISIONS[0]],
            isLoading: false,
            error: null,
        } as unknown as ReturnType<typeof useSpamDecisions>)

        const user = userEvent.setup()
        render(<RecentSpamTab />)

        // Open dialog
        const restoreBtn = screen.getByRole('button', { name: /restore/i })
        await user.click(restoreBtn)
        await waitFor(() => {
            expect(screen.getByRole('alertdialog')).toBeTruthy()
        })

        // Confirm inside dialog
        const confirmBtn = screen
            .getAllByRole('button', { name: /restore/i })
            .find((btn) => btn.closest('[role="alertdialog"]'))
        expect(confirmBtn).toBeTruthy()
        await user.click(confirmBtn!)

        expect(mockRestoreMutate).toHaveBeenCalledWith(
            'aaaaaaaa-0000-0000-0000-000000000001',
        )
    })
})
