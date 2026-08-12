/**
 * E2E spec: Recent Spam tab — toggle + table + Restore flow.
 *
 * Execution requires:
 *   - TWAKY_TEST_STACK_URL pointing at a running twaky stack
 *   - The `mail` sentinel already in the DB
 *   - A valid LemonLDAP-NG session cookie seeded via storageState / sign-session.py
 *
 * Preconditions (set up in beforeAll):
 *   - PATCH /api/mail-sentinel to set spam_filter_enabled: true via API.
 *   - Seed one spam_decision row via docker exec into twaky-api container.
 *
 * NOTE (SP6c T11 carry-over): this spec is lint-clean but execution is
 * deferred until the `.cache/uv` permission-denied issue inside the
 * twaky-api container is resolved (sign-session.py + seed scripts cannot
 * run inside the container in CI). The same blocker affects SP6 T29 and
 * SP6b T12. Document this status in task-11-report.md.
 */

import { test, expect } from './fixtures'
import { execSync } from 'node:child_process'

// ---------------------------------------------------------------------------
// Seed helper
// ---------------------------------------------------------------------------

function seedSpamDecision(): string {
    // Copy the seed script into the container and run it.
    execSync(
        `docker compose cp frontend/tests/e2e/seed-spam-decision.py twaky-api:/tmp/seed-spam-decision.py`,
        { cwd: process.cwd() + '/..' },
    )
    return execSync(
        `docker compose exec -T twaky-api uv run python /tmp/seed-spam-decision.py`,
        { cwd: process.cwd() + '/..' },
    ).toString().trim()
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.beforeAll(async () => {
    // Pre-copy the seed helper so individual tests can call it.
    execSync(
        `docker compose cp frontend/tests/e2e/seed-spam-decision.py twaky-api:/tmp/seed-spam-decision.py`,
        { cwd: process.cwd() + '/..' },
    )
})

test(
    'sentinels-mail-recent-spam: toggle ON, row visible, Restore flow completes',
    async ({ signedInPage: page }) => {
        // ── 0. Navigate to Recent Spam tab ─────────────────────────────────────
        await page.goto('/sentinels/mail?tab=recent-spam')
        await expect(
            page.getByRole('heading', { name: 'Mail Sentinel' }),
        ).toBeVisible({ timeout: 8_000 })

        // ── 1. Ensure spam_filter_enabled = true via API (idempotent PATCH) ────
        const apiBase = page.url().replace(/\/sentinels\/.*/, '')
        const cookies = await page.context().cookies()
        const sessionCookie = cookies.find((c) => c.name === 'twaky_session')
        const cookieHeader = sessionCookie
            ? `twaky_session=${sessionCookie.value}`
            : ''

        // Fetch current sentinel config_values
        const sentinelResp = await page.request.get(
            `${apiBase}/api/sentinels/mail`,
            { headers: { Cookie: cookieHeader } },
        )
        let currentConfigValues: Record<string, unknown> = {}
        if (sentinelResp.ok()) {
            const body = (await sentinelResp.json()) as {
                config_values?: Record<string, unknown>
            }
            currentConfigValues = body.config_values ?? {}
        }

        // PATCH to ensure spam_filter_enabled = true
        await page.request.patch(`${apiBase}/api/sentinels/mail`, {
            headers: {
                Cookie: cookieHeader,
                'Content-Type': 'application/json',
            },
            data: JSON.stringify({
                config_values: {
                    ...currentConfigValues,
                    spam_filter_enabled: true,
                },
            }),
        })

        // ── 2. Seed one spam_decision row ──────────────────────────────────────
        seedSpamDecision()

        // ── 3. Reload to pick up the seeded state ─────────────────────────────
        await page.goto('/sentinels/mail?tab=recent-spam')
        await expect(
            page.getByRole('heading', { name: 'Mail Sentinel' }),
        ).toBeVisible({ timeout: 8_000 })

        // ── 4. Verify spam filter toggle is ON ────────────────────────────────
        const toggle = page.getByRole('switch', { name: /spam filter/i })
        await expect(toggle).toBeVisible({ timeout: 6_000 })
        await expect(toggle).toBeChecked()

        // ── 5. Verify the seeded row is visible ───────────────────────────────
        // Seed script inserts sender "spammer@evil.example.com"
        await expect(
            page.getByText('spammer@evil.example.com'),
        ).toBeVisible({ timeout: 8_000 })

        // ── 6. Click Restore → confirm dialog appears ─────────────────────────
        const spamRow = page
            .getByRole('row')
            .filter({ hasText: 'spammer@evil.example.com' })
        await spamRow.getByRole('button', { name: 'Restore' }).click()

        // Alert dialog must appear
        await expect(
            page.getByRole('alertdialog'),
        ).toBeVisible({ timeout: 5_000 })
        await expect(
            page.getByText('Restore this email?'),
        ).toBeVisible()

        // ── 7. Confirm restore ─────────────────────────────────────────────────
        await page.getByRole('button', { name: 'Restore' }).last().click()

        // ── 8. Verify "Restored on" text appears in the row ───────────────────
        await expect(
            page.getByText(/Restored on/i),
        ).toBeVisible({ timeout: 8_000 })
    },
)

test(
    'sentinels-mail-recent-spam: toggle OFF shows "Spam filter is off" message',
    async ({ signedInPage: page }) => {
        // ── 1. Ensure spam_filter_enabled = false via API ──────────────────────
        const apiBase = page.url().replace(/\/sentinels\/.*/, '')
        const cookies = await page.context().cookies()
        const sessionCookie = cookies.find((c) => c.name === 'twaky_session')
        const cookieHeader = sessionCookie
            ? `twaky_session=${sessionCookie.value}`
            : ''

        const sentinelResp = await page.request.get(
            `${apiBase}/api/sentinels/mail`,
            { headers: { Cookie: cookieHeader } },
        )
        let currentConfigValues: Record<string, unknown> = {}
        if (sentinelResp.ok()) {
            const body = (await sentinelResp.json()) as {
                config_values?: Record<string, unknown>
            }
            currentConfigValues = body.config_values ?? {}
        }

        await page.request.patch(`${apiBase}/api/sentinels/mail`, {
            headers: {
                Cookie: cookieHeader,
                'Content-Type': 'application/json',
            },
            data: JSON.stringify({
                config_values: {
                    ...currentConfigValues,
                    spam_filter_enabled: false,
                },
            }),
        })

        // ── 2. Navigate to Recent Spam tab ────────────────────────────────────
        await page.goto('/sentinels/mail?tab=recent-spam')
        await expect(
            page.getByRole('heading', { name: 'Mail Sentinel' }),
        ).toBeVisible({ timeout: 8_000 })

        // ── 3. Toggle must be unchecked ───────────────────────────────────────
        const toggle = page.getByRole('switch', { name: /spam filter/i })
        await expect(toggle).toBeVisible({ timeout: 6_000 })
        await expect(toggle).not.toBeChecked()

        // ── 4. "Spam filter is off" message visible ───────────────────────────
        await expect(
            page.getByText(/spam filter is off/i),
        ).toBeVisible({ timeout: 5_000 })
    },
)
