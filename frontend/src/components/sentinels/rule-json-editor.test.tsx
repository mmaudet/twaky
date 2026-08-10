/**
 * Tests for RuleJsonEditor and validateRule helper.
 *
 * Strategy:
 * - validateRule is a pure function — test it directly for schema correctness.
 * - For the component: mock @monaco-editor/react with a <textarea> and verify
 *   that onChange is called with the right meta when the user types.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { validateRule } from './rule-json-editor'

// Mock @monaco-editor/react with a plain <textarea>.
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

import { RuleJsonEditor } from './rule-json-editor'

const VALID_RULE = JSON.stringify(
    {
        name: 'archive-newsletters',
        description: 'Archive newsletter emails',
        conditions: [
            { field: 'subject', operator: 'contains', value: 'unsubscribe' },
        ],
        combinator: 'OR',
        actions: ['archive'],
        priority: 50,
        enabled: true,
        run_on_threads: false,
    },
    null,
    2,
)

// ── validateRule (pure unit tests) ───────────────────────────────────────────

describe('validateRule', () => {
    it('returns isValid=true for a well-formed rule', () => {
        const result = validateRule(VALID_RULE)
        expect(result.isValid).toBe(true)
        expect(result.errors).toHaveLength(0)
    })

    it('returns isValid=false + parse error for malformed JSON', () => {
        const result = validateRule('{bad json}')
        expect(result.isValid).toBe(false)
        expect(result.errors.length).toBeGreaterThan(0)
    })

    it('returns isValid=false when required field "name" is missing', () => {
        const result = validateRule(JSON.stringify({ actions: ['archive'] }))
        expect(result.isValid).toBe(false)
        expect(result.errors.some((e) => e.includes('name') || e.includes('required'))).toBe(true)
    })

    it('returns isValid=false for an invalid action enum value', () => {
        const result = validateRule(
            JSON.stringify({ name: 'test', actions: ['not_a_valid_action'] }),
        )
        expect(result.isValid).toBe(false)
        expect(result.errors.length).toBeGreaterThan(0)
    })

    it('returns isValid=false for empty string', () => {
        const result = validateRule('')
        expect(result.isValid).toBe(false)
    })
})

// ── RuleJsonEditor component (interaction tests) ──────────────────────────────

describe('RuleJsonEditor', () => {
    it('shows no error panel when rendered with valid JSON', () => {
        const onChange = vi.fn()
        render(<RuleJsonEditor value={VALID_RULE} onChange={onChange} />)
        expect(screen.queryByRole('alert')).toBeNull()
    })

    it('shows error panel when rendered with malformed JSON', () => {
        const onChange = vi.fn()
        render(<RuleJsonEditor value="{bad json}" onChange={onChange} />)
        expect(screen.getByRole('alert')).toBeTruthy()
    })

    it('calls onChange with isValid=true when user types valid JSON', () => {
        const onChange = vi.fn()
        render(<RuleJsonEditor value="{bad}" onChange={onChange} />)

        const textarea = screen.getByRole('textbox', { name: 'monaco-editor' })
        fireEvent.change(textarea, { target: { value: VALID_RULE } })

        expect(onChange).toHaveBeenCalledWith(
            VALID_RULE,
            expect.objectContaining({ isValid: true, errors: [] }),
        )
    })

    it('calls onChange with isValid=false and surfaces error for malformed JSON', () => {
        const onChange = vi.fn()
        render(<RuleJsonEditor value={VALID_RULE} onChange={onChange} />)

        const textarea = screen.getByRole('textbox', { name: 'monaco-editor' })
        fireEvent.change(textarea, { target: { value: '{broken}' } })

        expect(onChange).toHaveBeenCalledWith(
            '{broken}',
            expect.objectContaining({ isValid: false }),
        )
        const lastMeta = onChange.mock.calls[onChange.mock.calls.length - 1][1] as {
            isValid: boolean
            errors: string[]
        }
        expect(lastMeta.errors.length).toBeGreaterThan(0)
        expect(screen.getByRole('alert')).toBeTruthy()
    })

    it('calls onChange with isValid=false when ajv rejects the rule', () => {
        const onChange = vi.fn()
        render(<RuleJsonEditor value={VALID_RULE} onChange={onChange} />)

        const textarea = screen.getByRole('textbox', { name: 'monaco-editor' })
        const missingName = JSON.stringify({ actions: ['archive'] })
        fireEvent.change(textarea, { target: { value: missingName } })

        expect(onChange).toHaveBeenCalledWith(
            missingName,
            expect.objectContaining({ isValid: false }),
        )
        const lastMeta = onChange.mock.calls[onChange.mock.calls.length - 1][1] as {
            errors: string[]
        }
        expect(lastMeta.errors.some((e: string) => e.includes('name') || e.includes('required'))).toBe(
            true,
        )
    })
})
