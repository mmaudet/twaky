import { test, expect } from './fixtures'

test('declare → detail → cancel', async ({ signedInPage: page }) => {
    await page.goto('/')

    // Open new-mission dialog + submit
    await page.getByRole('button', { name: /New mission/ }).click()
    const intent = `E2E test at ${new Date().toISOString()}`
    await page.getByRole('textbox').fill(intent)
    await page.getByRole('button', { name: /^Declare$/ }).click()

    // Router pushes to /missions/{id} after successful declare
    await page.waitForURL(/\/missions\//)
    await expect(page.getByRole('heading', { name: intent })).toBeVisible()

    // Cancel
    await page.getByRole('button', { name: /Cancel mission/ }).click()
    await page.getByRole('button', { name: /^Cancel mission$/ }).click()  // confirm

    // Back to dashboard
    await page.waitForURL('/', { timeout: 5000 })
})
