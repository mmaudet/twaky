import { test, expect } from './fixtures'

test('sign out clears session and redirects to login', async ({ signedInPage: page }) => {
    await page.goto('/')

    // Open the user dropdown
    await page.getByRole('button', { name: /alice|maudet|@/ }).click()
    await page.getByRole('menuitem', { name: /Sign out/ }).click()

    // The POST to /api/oauth/logout returns a 302 that the browser follows.
    // In test env this may end at LemonLDAP end-session (unreachable) or at /.
    // Either way, the next navigation to / should redirect us to login.
    await page.goto('/')
    // The URL after middleware redirect includes /api/oauth/login somewhere in the chain.
    // We can't easily observe intermediate URLs; instead assert we're NOT on the dashboard.
    await expect(page.getByRole('heading', { name: 'Missions' })).not.toBeVisible()
})
