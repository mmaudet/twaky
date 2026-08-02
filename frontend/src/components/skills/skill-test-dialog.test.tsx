import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { SkillTestDialog } from './skill-test-dialog'

const server = setupServer(
  http.post('http://localhost:3000/api/skills/:id/test', async ({ request }) => {
    const body = (await request.json()) as { args: Record<string, unknown> }
    if ((body.args as { fail?: boolean }).fail) {
      return HttpResponse.json({ outcome: 'error', message: 'ValueError: nope' })
    }
    return HttpResponse.json({ outcome: 'ok', result: body.args })
  }),
)

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function wrap(el: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{el}</QueryClientProvider>)
}

describe('SkillTestDialog', () => {
  it('renders a disabled button with tooltip when disabled', () => {
    wrap(<SkillTestDialog skillId="x" disabled tooltip="Save first" />)
    const btn = screen.getByRole('button', { name: /test/i })
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('title', 'Save first')
  })

  it('opens on click and shows args textarea', () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    expect(screen.getByLabelText(/args \(json object\)/i)).toBeInTheDocument()
  })

  it('shows JSON parse error for invalid input', () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    const ta = screen.getByLabelText(/args/i)
    fireEvent.change(ta, { target: { value: 'not-json' } })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))
    expect(screen.getByText(/Unexpected token|Invalid|Expected/i)).toBeInTheDocument()
  })

  it('shows outcome=ok and result on success', async () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    fireEvent.change(screen.getByLabelText(/args/i), { target: { value: '{"foo":"bar"}' } })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))
    await waitFor(() => expect(screen.getByText(/outcome: ok/i)).toBeInTheDocument())
    expect(screen.getByText(/"foo": "bar"/)).toBeInTheDocument()
  })

  it('shows outcome=error and message on failure', async () => {
    wrap(<SkillTestDialog skillId="x" />)
    fireEvent.click(screen.getByRole('button', { name: /test/i }))
    fireEvent.change(screen.getByLabelText(/args/i), { target: { value: '{"fail":true}' } })
    fireEvent.click(screen.getByRole('button', { name: /^run$/i }))
    await waitFor(() => expect(screen.getByText(/outcome: error/i)).toBeInTheDocument())
    expect(screen.getByText(/ValueError: nope/)).toBeInTheDocument()
  })
})
