import { test, expect } from './fixtures'

test('edit Plume temperature, save, verify in list', async ({ signedInPage: page }) => {
    // Navigate to /agents
    await page.goto('/agents')
    await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible()
    await expect(page.getByRole('cell', { name: 'Plume' })).toBeVisible()

    // Click Edit next to Plume.
    const plumeRow = page.getByRole('row').filter({ hasText: 'Plume' })
    await plumeRow.getByRole('link', { name: 'Edit' }).click()

    // On /agents/plume — change the temperature via the checkbox+slider.
    await expect(page.getByRole('heading', { name: /Edit Plume/i })).toBeVisible()

    // Ensure "Use LiteLLM default" is unchecked so the slider is active.
    const checkbox = page.getByLabel(/Use LiteLLM default/)
    if (await checkbox.isChecked()) await checkbox.click()

    // Interact with the slider via ARIA — set value to 0.3.
    // Radix Slider exposes role="slider" with aria-valuenow/min/max/step.
    const slider = page.getByRole('slider')
    await slider.focus()
    // Press Home to reach 0.0, then press ArrowRight 6 times (6 × 0.05 = 0.30).
    await slider.press('Home')
    for (let i = 0; i < 6; i++) await slider.press('ArrowRight')

    // Save.
    await page.getByRole('button', { name: 'Save' }).click()

    // Toast + redirect to /agents.
    await expect(page.getByText(/Saved\. Changes apply to the next mission\./)).toBeVisible()
    await expect(page).toHaveURL(/\/agents$/)

    // The Plume row now shows 0.30 in the Temperature column.
    const plumeRowAfter = page.getByRole('row').filter({ hasText: 'Plume' })
    await expect(plumeRowAfter).toContainText('0.30')

    // Cleanup: reset temperature back to null so subsequent runs are stable.
    await plumeRowAfter.getByRole('link', { name: 'Edit' }).click()
    await page.getByLabel(/Use LiteLLM default/).click()  // re-check
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page).toHaveURL(/\/agents$/)
})
