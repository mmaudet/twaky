import { test, expect, OWNER_EMAIL } from './fixtures'
import { execSync } from 'node:child_process'

function seedAwaitingUser(): string {
    // Runs the Python helper from inside twaky-api container.
    // Adjust the path if the file isn't mounted — for CI, we copy it into place.
    return execSync(
        `docker compose exec -T twaky-api uv run python /tmp/seed-awaiting-user.py ${OWNER_EMAIL}`,
        { cwd: process.cwd() + '/..' },
    ).toString().trim()
}

test.beforeAll(async () => {
    // Copy the seed helper into the container so `python` can find it.
    execSync(
        `docker compose cp frontend/tests/e2e/seed-awaiting-user.py twaky-api:/tmp/seed-awaiting-user.py`,
        { cwd: process.cwd() + '/..' },
    )
})

test('approve draft on awaiting_user mission', async ({ signedInPage: page }) => {
    const missionId = seedAwaitingUser()

    await page.goto(`/missions/${missionId}`)

    // Assert the ApproveDraftForm is present
    await expect(page.getByRole('heading', { name: 'Approve draft' })).toBeVisible()
    await expect(page.getByText('bob@x.com')).toBeVisible()
    await expect(page.getByText('Re: Question about widgets')).toBeVisible()

    // Approve as-is
    await page.getByRole('button', { name: /Approve/ }).click()

    // Success toast appears; SSE drives the state change. We only assert the
    // toast (state changes depend on whether a daemon is running).
    await expect(page.getByText(/approved/i)).toBeVisible({ timeout: 5000 })
})
