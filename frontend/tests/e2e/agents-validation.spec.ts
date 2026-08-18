import { test, expect } from './fixtures'

test('clearing prompt disables Save and turns counter red', async ({ signedInPage: page }) => {
    await page.goto('/agents/plume')
    await expect(page.getByRole('heading', { name: /Edit Plume/i })).toBeVisible()

    // Clear the prompt textarea.
    const prompt = page.getByLabel(/System prompt/i)
    await prompt.fill('')

    // Save disabled.
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()

    // Type a very long prompt — counter should turn red.
    await prompt.fill('x'.repeat(8001))
    const counter = page.getByText(/8,001 \/ 8,000/)
    await expect(counter).toBeVisible()
    // Assert the class, not the computed colour. Tailwind v4 defines its
    // palette in oklch, so getComputedStyle().color serialises as
    // "lab(48.4493 77.4328 61.5452)" — a moving target across Tailwind and
    // browser versions, and not what "turns red" actually means here.
    await expect(counter).toHaveClass(/text-red-600/)

    // Save also disabled at this state.
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()
})
