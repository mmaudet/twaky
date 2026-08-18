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
 * NOTE: the page ignores the `?tab=` query parameter — it renders
 * <Tabs defaultValue="rules"> — so these tests open the tab by clicking it.
 * (The older note here blamed a `.cache/uv` permission error inside
 * twaky-api. That one is fixed: the specs no longer shell out to `uv run`.)
 */

import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'
import { execSync } from 'node:child_process'

// ---------------------------------------------------------------------------
// Seed helper
// ---------------------------------------------------------------------------

type SeededDecision = { id: string; subject: string }

function seedSpamDecision(): SeededDecision {
    // Copy the seed script into the container and run it.
    execSync(
        `docker compose cp frontend/tests/e2e/seed-spam-decision.py twaky-api:/tmp/seed-spam-decision.py`,
        { cwd: process.cwd() + '/..' },
    )
    const out = execSync(
        `docker compose exec -T twaky-api python /tmp/seed-spam-decision.py`,
        { cwd: process.cwd() + '/..' },
    ).toString().trim()
    return JSON.parse(out) as SeededDecision
}

/** Open the Recent Spam tab. The `?tab=` query parameter is not honoured. */
async function openRecentSpamTab(page: Page): Promise<void> {
    await page.goto('/sentinels/mail')
    await expect(
        page.getByRole('heading', { name: 'Mail Sentinel' }),
    ).toBeVisible({ timeout: 8_000 })
    await page.getByRole('tab', { name: 'Recent Spam' }).click()
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
    'sentinels-mail-recent-spam: toggle ON, seeded row visible, Restore dialog opens',
    async ({ signedInPage: page }) => {
        // ── 0. Navigate to Recent Spam tab ─────────────────────────────────────
        await openRecentSpamTab(page)

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
        // Decisions accumulate, so target this seed's own subject rather than
        // the fixed sender address every seeded row shares.
        const seeded = seedSpamDecision()

        // ── 3. Reload to pick up the seeded state ─────────────────────────────
        await openRecentSpamTab(page)

        // ── 4. Verify spam filter toggle is ON ────────────────────────────────
        const toggle = page.getByRole('switch', { name: /spam filter/i })
        await expect(toggle).toBeVisible({ timeout: 6_000 })
        await expect(toggle).toBeChecked()

        // ── 5. Verify the seeded row is visible ───────────────────────────────
        await expect(
            page.getByText(seeded.subject),
        ).toBeVisible({ timeout: 8_000 })

        // ── 6. Click Restore → confirm dialog appears ─────────────────────────
        const spamRow = page.getByRole('row').filter({ hasText: seeded.subject })
        await spamRow.getByRole('button', { name: 'Restore' }).click()

        // Alert dialog must appear
        await expect(
            page.getByRole('alertdialog'),
        ).toBeVisible({ timeout: 5_000 })
        await expect(
            page.getByText('Restore this email?'),
        ).toBeVisible()

        // ── 7. Confirm restore ─────────────────────────────────────────────────
        // Everything above runs anywhere. Confirming does not: the endpoint
        // builds a JmapMailAdapter and moves the message back in the real
        // mailbox, so without a connected account it answers 502 and no
        // "Restored on" ever appears. Opt in against a real stack.
        test.skip(
            !process.env.TWAKY_E2E_LIVE_JMAP,
            'completing a restore writes to a real mailbox; ' +
                'set TWAKY_E2E_LIVE_JMAP=1 to run',
        )
        await page.getByRole('button', { name: 'Restore' }).last().click()

        // ── 8. Verify "Restored on" text appears in the row ───────────────────
        await expect(
            spamRow.getByText(/Restored on/i),
        ).toBeVisible({ timeout: 8_000 })
    },
)

test(
    'sentinels-mail-recent-spam: toggle OFF shows "Spam filter is off" message',
    async ({ signedInPage: page }) => {
        // ── 1. Ensure spam_filter_enabled = false via API ──────────────────────
        // Navigate first: page.url() is "about:blank" until then, and deriving
        // apiBase from it yields a URL page.request refuses ("Protocol
        // \"about:\" not supported").
        await openRecentSpamTab(page)
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

        // ── 2. Reload so the UI reflects the PATCH ────────────────────────────
        await openRecentSpamTab(page)

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
