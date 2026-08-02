import { test, expect } from './fixtures'

test('run skill via Test dialog, verify outcome=ok', async ({ signedInPage: page }) => {
    // Seed a skill via the API — use page.request so the auth cookie is shared
    const created = await page.request.post('/api/skills', {
        data: {
            name: 'e2e_testable',
            description: 'Testable E2E skill',
            python_source: 'def run(**kwargs):\n    return {"echo": kwargs}',
            bound_agents: [],
            enabled: true,
        },
    })
    expect(created.ok()).toBeTruthy()
    const { id } = await created.json()

    try {
        // Navigate to the skill edit page
        await page.goto(`/skills/${id}`)

        // Open the Test dialog
        await page.getByRole('button', { name: /^test$/i }).click()

        // Fill the args JSON textarea (label htmlFor="test-args" → "Args (JSON object)")
        const argsBox = page.getByLabel(/args \(json object\)/i)
        await argsBox.fill('{"foo": "bar"}')

        // Click Run
        await page.getByRole('button', { name: /^run$/i }).click()

        // Assert outcome=ok badge is visible
        await expect(page.getByText(/outcome: ok/i)).toBeVisible()

        // Assert result <pre> contains the echoed key
        await expect(page.locator('pre')).toContainText('"foo": "bar"')
    } finally {
        // Cleanup: delete the seeded skill even if assertions fail
        await page.request.delete(`/api/skills/${id}`)
    }
})
