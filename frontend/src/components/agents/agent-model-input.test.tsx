import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentModelInput } from './agent-model-input'

describe('AgentModelInput', () => {
    it('renders "Use default" when value is null', () => {
        render(<AgentModelInput value={null} onChange={() => {}} effectiveDefault="X" />)
        expect(screen.getByText(/Use default \(X\)/)).toBeInTheDocument()
    })

    it('does not render custom input when a known model is selected', () => {
        render(
            <AgentModelInput
                value="openai/gpt-4o"
                onChange={() => {}}
                effectiveDefault="X"
            />,
        )
        expect(screen.queryByLabelText(/custom model string/i)).not.toBeInTheDocument()
    })

    it('shows custom input when value is not in the known list', () => {
        render(
            <AgentModelInput
                value="exotic/private-model-v2"
                onChange={() => {}}
                effectiveDefault="X"
            />,
        )
        expect(screen.getByLabelText(/custom model string/i)).toHaveValue('exotic/private-model-v2')
    })

    it('calls onChange(null) when the user picks Use default', () => {
        const onChange = vi.fn()
        render(
            <AgentModelInput value="openai/gpt-4o" onChange={onChange} effectiveDefault="X" />,
        )
        // simulate via Select — Radix uses role="combobox"
        const combobox = screen.getByRole('combobox')
        fireEvent.click(combobox)
        fireEvent.click(screen.getByText(/Use default \(X\)/))
        expect(onChange).toHaveBeenLastCalledWith(null)
    })

    it('calls onChange with typed value when using Custom…', () => {
        const onChange = vi.fn()
        render(
            <AgentModelInput
                value="some/exotic-value"
                onChange={onChange}
                effectiveDefault="X"
            />,
        )
        const input = screen.getByLabelText(/custom model string/i)
        fireEvent.change(input, { target: { value: 'foo/bar' } })
        expect(onChange).toHaveBeenLastCalledWith('foo/bar')
    })
})
