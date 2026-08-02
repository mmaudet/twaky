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
    // Counter has the red class (test via CSS color computed style).
    const color = await counter.evaluate((el) => getComputedStyle(el).color)
    expect(color).toMatch(/rgb\(220, 38, 38\)|rgb\(185, 28, 28\)/)  // Tailwind red-600 or red-700

    // Save also disabled at this state.
    await expect(page.getByRole('button', { name: 'Save' })).toBeDisabled()
})
