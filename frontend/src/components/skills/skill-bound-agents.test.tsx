import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SkillBoundAgents } from './skill-bound-agents'

describe('SkillBoundAgents', () => {
  it('checks the boxes for bound agents', () => {
    render(<SkillBoundAgents value={['atlas', 'plume']} onChange={() => {}} />)
    expect(screen.getByLabelText('atlas')).toBeChecked()
    expect(screen.getByLabelText('plume')).toBeChecked()
    expect(screen.getByLabelText('chronos')).not.toBeChecked()
    expect(screen.getByLabelText('iris')).not.toBeChecked()
  })

  it('adds agent on check', () => {
    const onChange = vi.fn()
    render(<SkillBoundAgents value={['atlas']} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('plume'))
    expect(onChange).toHaveBeenCalledWith(['atlas', 'plume'])
  })

  it('removes agent on uncheck', () => {
    const onChange = vi.fn()
    render(<SkillBoundAgents value={['atlas', 'plume']} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('atlas'))
    expect(onChange).toHaveBeenCalledWith(['plume'])
  })
})
