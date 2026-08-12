/**
 * Smoke tests for the Rule Editor page (Propose/Apply flow).
 *
 * Strategy: mock all hooks and Next.js navigation. Monaco is mocked with
 * a plain <textarea> matching the pattern in rule-json-editor.test.tsx.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// ── Next.js navigation mocks ─────────────────────────────────────────────────

const mockPush = vi.fn()

vi.mock('next/navigation', () => ({
    useRouter: () => ({ push: mockPush }),
    useParams: vi.fn(),
}))

vi.mock('next/link', () => ({
    default: ({
        href,
        children,
    }: {
        href: string
        children: React.ReactNode
    }) => <a href={href}>{children}</a>,
}))

// ── Monaco mock ───────────────────────────────────────────────────────────────

vi.mock('@monaco-editor/react', () => ({
    default: ({
        value,
        onChange,
    }: {
        value?: string
        onChange?: (v: string) => void
    }) => (
        <textarea
            aria-label="monaco-editor"
            defaultValue={value}
            onChange={(e) => onChange?.(e.target.value)}
        />
    ),
}))

// ── Hook mocks ────────────────────────────────────────────────────────────────

const mockCreateMutateAsync = vi.fn()
const mockPatchMutateAsync = vi.fn()
const mockDeleteMutateAsync = vi.fn()
const mockProposeMutateAsync = vi.fn()

vi.mock('@/hooks/use-mail-sentinel-rules', () => ({
    useMailRule: vi.fn(),
    useCreateMailRule: vi.fn(),
    usePatchMailRule: vi.fn(),
    useDeleteMailRule: vi.fn(),
    useProposeMailRule: vi.fn(),
}))

import { useParams } from 'next/navigation'
import {
    useMailRule,
    useCreateMailRule,
    usePatchMailRule,
    useDeleteMailRule,
    useProposeMailRule,
} from '@/hooks/use-mail-sentinel-rules'
import RuleEditorPage from './page'

const mockUseParams = vi.mocked(useParams)
const mockUseMailRule = vi.mocked(useMailRule)
const mockUseCreateMailRule = vi.mocked(useCreateMailRule)
const mockUsePatchMailRule = vi.mocked(usePatchMailRule)
const mockUseDeleteMailRule = vi.mocked(useDeleteMailRule)
const mockUseProposeMailRule = vi.mocked(useProposeMailRule)

const PROPOSE_SUCCESS_RESULT = {
    valid: true,
    matched_count: 5,
    would_shadow_count: 0,
    would_shadow: [],
    matched_examples: [
        {
            decision_id: 'dec-1',
            sender: 'spam@example.com',
            subject: 'Buy this!',
            current_bucket: 'spam',
            would_shadow_by: null,
        },
    ],
    simulation_partial: false,
    simulation_partial_reason: null,
}

const EXISTING_RULE = {
    id: 'rule-uuid-1',
    name: 'test-rule',
    description: 'A test rule',
    conditions: [{ field: 'from', operator: 'contains', value: '@evil.com' }],
    combinator: 'OR',
    actions: ['archive'],
    priority: 100,
    enabled: true,
    run_on_threads: false,
}

function setupHooks(isNew: boolean) {
    mockUseParams.mockReturnValue({ id: isNew ? 'new' : 'rule-uuid-1' })

    mockUseMailRule.mockReturnValue({
        data: isNew ? undefined : EXISTING_RULE,
        isLoading: false,
        error: null,
    } as unknown as ReturnType<typeof useMailRule>)

    mockUseCreateMailRule.mockReturnValue({
        mutateAsync: mockCreateMutateAsync,
        isPending: false,
    } as unknown as ReturnType<typeof useCreateMailRule>)

    mockUsePatchMailRule.mockReturnValue({
        mutateAsync: mockPatchMutateAsync,
        isPending: false,
    } as unknown as ReturnType<typeof usePatchMailRule>)

    mockUseDeleteMailRule.mockReturnValue({
        mutateAsync: mockDeleteMutateAsync,
        isPending: false,
    } as unknown as ReturnType<typeof useDeleteMailRule>)

    mockUseProposeMailRule.mockReturnValue({
        mutateAsync: mockProposeMutateAsync,
        isPending: false,
    } as unknown as ReturnType<typeof useProposeMailRule>)
}

beforeEach(() => {
    vi.clearAllMocks()
    mockProposeMutateAsync.mockResolvedValue(PROPOSE_SUCCESS_RESULT)
    mockCreateMutateAsync.mockResolvedValue({})
    mockPatchMutateAsync.mockResolvedValue({})
})

// ── isNew case ────────────────────────────────────────────────────────────────

describe('RuleEditorPage (isNew=true)', () => {
    it('shows Preview matches button; no Apply button initially', () => {
        setupHooks(true)
        render(<RuleEditorPage />)

        expect(screen.getByRole('button', { name: /preview matches/i })).toBeTruthy()
        expect(screen.queryByRole('button', { name: /apply rule/i })).toBeNull()
    })

    it('does not show Save rule button', () => {
        setupHooks(true)
        render(<RuleEditorPage />)

        expect(screen.queryByRole('button', { name: /save rule/i })).toBeNull()
    })

    it('after successful Propose, Apply appears but is disabled', async () => {
        setupHooks(true)
        const user = userEvent.setup()
        render(<RuleEditorPage />)

        await user.click(screen.getByRole('button', { name: /preview matches/i }))

        await waitFor(() => {
            expect(screen.getByRole('button', { name: /apply rule/i })).toBeTruthy()
        })

        const applyBtn = screen.getByRole('button', { name: /apply rule/i })
        expect(applyBtn).toHaveAttribute('disabled')
    })

    it('checking reviewed checkbox enables Apply button', async () => {
        setupHooks(true)
        const user = userEvent.setup()
        render(<RuleEditorPage />)

        await user.click(screen.getByRole('button', { name: /preview matches/i }))
        await waitFor(() => {
            expect(screen.getByRole('checkbox', { name: /i have reviewed the matches/i })).toBeTruthy()
        })

        await user.click(screen.getByRole('checkbox', { name: /i have reviewed the matches/i }))

        const applyBtn = screen.getByRole('button', { name: /apply rule/i })
        expect(applyBtn).not.toHaveAttribute('disabled')
    })

    it('clicking Apply calls create.mutateAsync', async () => {
        setupHooks(true)
        const user = userEvent.setup()
        render(<RuleEditorPage />)

        await user.click(screen.getByRole('button', { name: /preview matches/i }))
        await waitFor(() => {
            expect(screen.getByRole('checkbox', { name: /i have reviewed the matches/i })).toBeTruthy()
        })

        await user.click(screen.getByRole('checkbox', { name: /i have reviewed the matches/i }))
        await user.click(screen.getByRole('button', { name: /apply rule/i }))

        await waitFor(() => {
            expect(mockCreateMutateAsync).toHaveBeenCalledTimes(1)
        })
        expect(mockPatchMutateAsync).not.toHaveBeenCalled()
    })
})

// ── Edit case ─────────────────────────────────────────────────────────────────

describe('RuleEditorPage (edit existing rule)', () => {
    it('shows Preview matches button; no Apply button initially', () => {
        setupHooks(false)
        render(<RuleEditorPage />)

        expect(screen.getByRole('button', { name: /preview matches/i })).toBeTruthy()
        expect(screen.queryByRole('button', { name: /apply rule/i })).toBeNull()
    })

    it('does not show Save rule button', () => {
        setupHooks(false)
        render(<RuleEditorPage />)

        expect(screen.queryByRole('button', { name: /save rule/i })).toBeNull()
    })

    it('after successful Propose, Apply appears but is disabled', async () => {
        setupHooks(false)
        const user = userEvent.setup()
        render(<RuleEditorPage />)

        await user.click(screen.getByRole('button', { name: /preview matches/i }))

        await waitFor(() => {
            expect(screen.getByRole('button', { name: /apply rule/i })).toBeTruthy()
        })

        const applyBtn = screen.getByRole('button', { name: /apply rule/i })
        expect(applyBtn).toHaveAttribute('disabled')
    })

    it('checking reviewed checkbox enables Apply button', async () => {
        setupHooks(false)
        const user = userEvent.setup()
        render(<RuleEditorPage />)

        await user.click(screen.getByRole('button', { name: /preview matches/i }))
        await waitFor(() => {
            expect(screen.getByRole('checkbox', { name: /i have reviewed the matches/i })).toBeTruthy()
        })

        await user.click(screen.getByRole('checkbox', { name: /i have reviewed the matches/i }))

        const applyBtn = screen.getByRole('button', { name: /apply rule/i })
        expect(applyBtn).not.toHaveAttribute('disabled')
    })

    it('clicking Apply calls patch.mutateAsync (not create)', async () => {
        setupHooks(false)
        const user = userEvent.setup()
        render(<RuleEditorPage />)

        await user.click(screen.getByRole('button', { name: /preview matches/i }))
        await waitFor(() => {
            expect(screen.getByRole('checkbox', { name: /i have reviewed the matches/i })).toBeTruthy()
        })

        await user.click(screen.getByRole('checkbox', { name: /i have reviewed the matches/i }))
        await user.click(screen.getByRole('button', { name: /apply rule/i }))

        await waitFor(() => {
            expect(mockPatchMutateAsync).toHaveBeenCalledTimes(1)
        })
        expect(mockCreateMutateAsync).not.toHaveBeenCalled()
    })

    it('Delete rule button is present (delete not gated by Propose)', () => {
        setupHooks(false)
        render(<RuleEditorPage />)

        expect(screen.getByRole('button', { name: /delete rule/i })).toBeTruthy()
    })
})

// ── JSON drift invalidates ProposeResults ─────────────────────────────────────

describe('RuleEditorPage (JSON drift collapses ProposeResults)', () => {
    it('editing the JSON after a Propose collapses the panel and hides Apply', async () => {
        setupHooks(true)
        const user = userEvent.setup()
        render(<RuleEditorPage />)

        // Run Propose
        await user.click(screen.getByRole('button', { name: /preview matches/i }))
        await waitFor(() => {
            expect(screen.getByRole('button', { name: /apply rule/i })).toBeTruthy()
        })

        // Simulate editing the JSON in the Monaco textarea
        const textarea = screen.getByRole('textbox', { name: 'monaco-editor' })
        fireEvent.change(textarea, {
            target: { value: '{"name":"changed-rule","conditions":[],"combinator":"OR","actions":["archive"],"priority":50,"enabled":true,"run_on_threads":false}' },
        })

        // Apply button and ProposeResults should be gone
        expect(screen.queryByRole('button', { name: /apply rule/i })).toBeNull()
        expect(screen.queryByRole('checkbox', { name: /i have reviewed the matches/i })).toBeNull()
    })
})
