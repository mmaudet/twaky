import { test, expect } from './fixtures'
import { execSync } from 'node:child_process'

type SeededRun = { id: string; event_ref: string }

function seedSentinelRun(): SeededRun {
    // Copy the seed script into the container and run it.
    execSync(
        `docker compose cp frontend/tests/e2e/seed-sentinel-run.py twaky-api:/tmp/seed-sentinel-run.py`,
        { cwd: process.cwd() + '/..' },
    )
    const out = execSync(
        `docker compose exec -T twaky-api python /tmp/seed-sentinel-run.py`,
        { cwd: process.cwd() + '/..' },
    ).toString().trim()
    return JSON.parse(out) as SeededRun
}

test.beforeAll(async () => {
    // Pre-copy the seed helper so individual tests can call it without repetition.
    execSync(
        `docker compose cp frontend/tests/e2e/seed-sentinel-run.py twaky-api:/tmp/seed-sentinel-run.py`,
        { cwd: process.cwd() + '/..' },
    )
})

test('mail-sentinel-run-detail: runs tab shows a run, click Detail opens run detail page with trace', async ({ signedInPage: page }) => {
    // Seed a run so the Runs tab is guaranteed to have at least one entry.
    // Runs accumulate across executions, so every assertion below targets this
    // run's own event_ref rather than the shared "e2e-seed-" prefix.
    const seeded = seedSentinelRun()

    // Navigate to the Mail Sentinel page and switch to the Runs tab.
    await page.goto('/sentinels/mail')
    await expect(page.getByRole('heading', { name: 'Mail Sentinel' })).toBeVisible()
    await page.getByRole('tab', { name: 'Runs' }).click()

    // The seeded run must appear in the table.
    await expect(page.getByText(seeded.event_ref)).toBeVisible({ timeout: 5000 })

    // Click the Detail button for that row.
    const runRow = page.getByRole('row').filter({ hasText: seeded.event_ref })
    await runRow.getByRole('link', { name: 'Detail' }).click()

    // Must navigate to /sentinels/mail/runs/<uuid>
    await expect(page).toHaveURL(/\/sentinels\/mail\/runs\/[0-9a-f-]{36}$/, { timeout: 5000 })

    // The run detail page must show the sentinel name and outcome.
    // exact: true — a substring match on 'mail' also hits the helper copy
    // further down the page, which mentions "email".
    await expect(page.getByText('mail', { exact: true })).toBeVisible()
    // Outcome badge: "processed"
    await expect(page.getByText('processed')).toBeVisible()

    // The event_ref section must be present.
    await expect(page.getByRole('heading', { name: 'Event ref' })).toBeVisible()
    await expect(page.getByText(seeded.event_ref)).toBeVisible()

    // Trace section must show the two nodes we seeded. SP5c renders friendly
    // labels from NODE_META instead of the raw node names, so assert what the
    // user actually sees; a node with no entry falls back to its raw name.
    await expect(page.getByRole('heading', { name: 'Trace' })).toBeVisible()
    await expect(page.getByText('Match rules', { exact: true })).toBeVisible()
    await expect(page.getByText('done', { exact: true })).toBeVisible()
})

test('mail-sentinel-run-detail: navigating to a non-existent run shows not-found message', async ({ signedInPage: page }) => {
    const fakeId = '00000000-0000-0000-0000-000000000000'
    await page.goto(`/sentinels/mail/runs/${fakeId}`)
    // The page renders "Run not found." for a 404 from the API.
    await expect(page.getByText(/not found/i)).toBeVisible({ timeout: 5000 })
})
