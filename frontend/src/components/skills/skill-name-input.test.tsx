import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SkillNameInput } from './skill-name-input'

describe('SkillNameInput', () => {
  it('accepts a valid name silently', () => {
    render(<SkillNameInput value="echo" onChange={() => {}} />)
    expect(screen.queryByText(/Must match/)).toBeNull()
  })

  it('shows error on invalid name', () => {
    render(<SkillNameInput value="Echo" onChange={() => {}} />)
    expect(screen.getByText(/Must match/)).toBeInTheDocument()
  })

  it('accepts empty string silently (pre-input state)', () => {
    render(<SkillNameInput value="" onChange={() => {}} />)
    expect(screen.queryByText(/Must match/)).toBeNull()
  })

  it('emits onChange on user input', () => {
    const onChange = vi.fn()
    render(<SkillNameInput value="" onChange={onChange} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'x' } })
    expect(onChange).toHaveBeenCalledWith('x')
  })
})
