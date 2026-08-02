import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentPromptInput } from './agent-prompt-input'

describe('AgentPromptInput', () => {
    it('renders textarea with the value', () => {
        render(<AgentPromptInput value="hello" onChange={() => {}} />)
        expect(screen.getByLabelText(/system prompt/i)).toHaveValue('hello')
    })

    it('calls onChange when typing', () => {
        const onChange = vi.fn()
        render(<AgentPromptInput value="" onChange={onChange} />)
        fireEvent.change(screen.getByLabelText(/system prompt/i), { target: { value: 'x' } })
        expect(onChange).toHaveBeenCalledWith('x')
    })

    it('shows counter in default color under limit', () => {
        render(<AgentPromptInput value="hello" onChange={() => {}} />)
        expect(screen.getByText(/5 \/ 8,000/)).toBeInTheDocument()
        expect(screen.getByText(/5 \/ 8,000/)).not.toHaveClass('text-red-600')
    })

    it('shows counter in red when over limit', () => {
        render(<AgentPromptInput value={'x'.repeat(8001)} onChange={() => {}} />)
        const counter = screen.getByText(/8,001 \/ 8,000/)
        expect(counter).toHaveClass('text-red-600')
    })
})
