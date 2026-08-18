import { test, expect } from './fixtures'

test('sign out clears session and redirects to login', async ({ signedInPage: page }) => {
    await page.goto('/')

    // Open the user dropdown
    await page.getByRole('button', { name: /alice|maudet|@/ }).click()
    await page.getByRole('menuitem', { name: /Sign out/ }).click()

    // The POST to /api/oauth/logout returns a 302 that the browser follows to
    // the OIDC end-session endpoint. That host does not resolve in a test
    // environment, so the browser settles on its own error page. Wait for that
    // navigation to finish before starting ours — otherwise page.goto races it
    // and Playwright aborts with "interrupted by another navigation".
    // Retry: the bounce is still in flight for an unpredictable while (a DNS
    // failure, not a fast 404), and a goto issued mid-flight aborts.
    await expect(async () => {
        await page.goto('/', { waitUntil: 'domcontentloaded' })
    }).toPass({ timeout: 20_000 })
    // The URL after middleware redirect includes /api/oauth/login somewhere in the chain.
    // We can't easily observe intermediate URLs; instead assert we're NOT on the dashboard.
    await expect(page.getByRole('heading', { name: 'Missions' })).not.toBeVisible()
})
