import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { AgentTemperatureInput } from './agent-temperature-input'

describe('AgentTemperatureInput', () => {
    it('disables slider and shows em-dash when value is null', () => {
        render(<AgentTemperatureInput value={null} onChange={() => {}} />)
        expect(screen.getByRole('checkbox')).toBeChecked()
        expect(screen.getByText('—')).toBeInTheDocument()
    })

    it('shows numeric readout when value is set', () => {
        render(<AgentTemperatureInput value={0.7} onChange={() => {}} />)
        expect(screen.getByText('0.70')).toBeInTheDocument()
        expect(screen.getByRole('checkbox')).not.toBeChecked()
    })

    it('calls onChange(null) when checkbox is checked', () => {
        const onChange = vi.fn()
        render(<AgentTemperatureInput value={0.7} onChange={onChange} />)
        fireEvent.click(screen.getByRole('checkbox'))
        expect(onChange).toHaveBeenCalledWith(null)
    })

    it('calls onChange(0.7) when checkbox is unchecked from null', () => {
        const onChange = vi.fn()
        render(<AgentTemperatureInput value={null} onChange={onChange} />)
        fireEvent.click(screen.getByRole('checkbox'))
        expect(onChange).toHaveBeenCalledWith(0.7)
    })
})
