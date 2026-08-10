import { test, expect } from './fixtures'

test('sentinel toggle: mail row visible, click switch, PATCH fires, state persists on reload', async ({ signedInPage: page }) => {
    await page.goto('/sentinels')
    await expect(page.getByRole('heading', { name: 'Sentinels' })).toBeVisible()

    // Verify the mail sentinel row exists
    const mailRow = page.getByRole('row').filter({ hasText: 'mail' })
    await expect(mailRow).toBeVisible()

    // Read the current switch state
    const toggle = mailRow.getByRole('switch')
    const wasPreviouslyEnabled = await toggle.isChecked()

    // Intercept the PATCH request to verify it fires
    const patchPromise = page.waitForRequest(
        (req) => req.url().includes('/api/sentinels/mail') && req.method() === 'PATCH',
    )

    // Click the switch to toggle
    await toggle.click()

    // Verify the PATCH request was fired
    const patchReq = await patchPromise
    const body = patchReq.postDataJSON() as { enabled: boolean }
    // The PATCH body must contain `enabled` set to the NEW value (opposite of previous)
    expect(typeof body.enabled).toBe('boolean')
    expect(body.enabled).toBe(!wasPreviouslyEnabled)

    // Wait for the response to settle (the toast confirms the update)
    await expect(page.getByText(/enabled|disabled/i)).toBeVisible({ timeout: 5000 })

    // Reload and verify the toggle state was persisted
    await page.goto('/sentinels')
    const mailRowAfter = page.getByRole('row').filter({ hasText: 'mail' })
    const toggleAfter = mailRowAfter.getByRole('switch')
    expect(await toggleAfter.isChecked()).toBe(!wasPreviouslyEnabled)

    // Restore original state so the test is idempotent
    const restorePromise = page.waitForRequest(
        (req) => req.url().includes('/api/sentinels/mail') && req.method() === 'PATCH',
    )
    await toggleAfter.click()
    await restorePromise
    await expect(page.getByText(/enabled|disabled/i)).toBeVisible({ timeout: 5000 })
})
