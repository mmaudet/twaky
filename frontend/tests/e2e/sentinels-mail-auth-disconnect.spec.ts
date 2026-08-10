/**
 * E2E spec: Disconnect JMAP account via the Auth tab.
 *
 * Precondition: the account must already be connected.  This spec skips
 * gracefully when not connected rather than trying to seed a real OAuth
 * credential (which would require a live refresh_token captured from the UI
 * or DevTools — operator-only, not automatable in unattended CI).
 *
 * NOTE (SP6b T29 carry-over): this spec is lint-clean but execution is deferred
 * until the `.cache/uv` permission-denied issue inside the twaky-api container
 * is resolved (sign-session.py cannot run inside the container in CI).
 * Document this status in the task-12-report.md.
 */

import { test, expect } from './fixtures'

test(
    'sentinels-mail-auth-disconnect: disconnect removes credential and shows Not connected',
    async ({ signedInPage: page }) => {
        // ── 0. Navigate to Auth tab ────────────────────────────────────────────
        await page.goto('/sentinels/mail?tab=auth')

        // ── 1. Skip if not connected ──────────────────────────────────────────
        // Poll the status API before touching the UI.
        const apiBase = page.url().replace(/\/sentinels\/.*/, '')
        const cookie = await page.context().cookies()
        const sessionCookie = cookie.find((c) => c.name === 'twaky_session')

        let isConnected = false
        if (sessionCookie) {
            const statusResp = await page.request.get(
                `${apiBase}/api/mail-sentinel/auth`,
                { headers: { Cookie: `twaky_session=${sessionCookie.value}` } },
            )
            if (statusResp.ok()) {
                const body = (await statusResp.json()) as { connected?: boolean }
                isConnected = !!body.connected
            }
        }

        if (!isConnected) {
            test.skip(
                true,
                'JMAP account not connected — run sentinels-mail-auth-connect first',
            )
            return
        }

        // ── 2. Assert connected state visible ─────────────────────────────────
        // The Auth tab already shows the connected card; reload to be sure.
        await page.reload()
        const disconnectBtn = page.getByRole('button', { name: 'Disconnect' })
        await expect(disconnectBtn).toBeVisible({ timeout: 8_000 })

        // ── 3. Click Disconnect → AlertDialog appears ─────────────────────────
        await disconnectBtn.click()

        // The confirmation dialog title
        await expect(
            page.getByText('Disconnect JMAP account?'),
        ).toBeVisible({ timeout: 5_000 })

        // ── 4. Confirm disconnect ─────────────────────────────────────────────
        // The AlertDialogAction has the label "Disconnect" (same text, so use last())
        await page
            .getByRole('button', { name: /^disconnect$/i })
            .last()
            .click()

        // ── 5. Assert disconnected state ──────────────────────────────────────
        // The card switches to "Not connected" mode (the Connect button appears).
        await expect(
            page.getByRole('button', { name: 'Connect JMAP account' }),
        ).toBeVisible({ timeout: 10_000 })
    },
)
