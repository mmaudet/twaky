/**
 * E2E spec: Connect JMAP account via the Auth tab.
 *
 * Execution requires:
 *   - TWAKY_TEST_STACK_URL pointing at a running twaky stack
 *   - A valid LemonLDAP-NG session cookie seeded via storageState (the OIDC
 *     redirect is pre-authenticated by the operator's browser session, so no
 *     login prompt appears during the OAuth code flow)
 *   - The `mail` sentinel already in the DB with `JMAP_OAUTH_*` env vars set
 *
 * NOT RUNNABLE IN CI, BY NATURE. Step 3 drives a real OAuth authorize round
 * trip against auth.twake-dev.maudet.cloud and depends on an operator's
 * LemonLDAP-NG session already being present in storageState. A CI runner has
 * neither, and pointing CI at the deployment's IDP would be the wrong fix. It
 * is therefore opt-in: set TWAKY_E2E_LIVE_OIDC=1 to run it against a real
 * stack.
 *
 * (The older note here blamed a `.cache/uv` permission error inside
 * twaky-api. That one is fixed — the specs no longer shell out to `uv run`.)
 */

import { test, expect } from './fixtures'

test.skip(
    !process.env.TWAKY_E2E_LIVE_OIDC,
    'needs a real IDP and a pre-authenticated operator session; ' +
        'set TWAKY_E2E_LIVE_OIDC=1 to run',
)

test(
    'sentinels-mail-auth-connect: connect JMAP account via OAuth flow',
    async ({ signedInPage: page }) => {
        // ── 0. Navigate to the Auth tab ────────────────────────────────────────
        await page.goto('/sentinels/mail?tab=auth')

        // ── 1. Idempotency guard: disconnect if already connected ──────────────
        // If a previous run left the account connected, DELETE via API so we
        // always start from the "Not connected" state.
        const apiBase = page.url().replace(/\/sentinels\/.*/, '')
        const cookie = await page.context().cookies()
        const sessionCookie = cookie.find((c) => c.name === 'twaky_session')
        if (sessionCookie) {
            const statusResp = await page.request.get(
                `${apiBase}/api/mail-sentinel/auth`,
                { headers: { Cookie: `twaky_session=${sessionCookie.value}` } },
            )
            if (statusResp.ok()) {
                const body = (await statusResp.json()) as { connected?: boolean }
                if (body.connected) {
                    await page.request.delete(
                        `${apiBase}/api/mail-sentinel/auth`,
                        {
                            headers: {
                                Cookie: `twaky_session=${sessionCookie.value}`,
                            },
                        },
                    )
                    // Reload so the UI reflects the disconnected state
                    await page.reload()
                }
            }
        }

        // ── 2. Assert "Connect JMAP account" button is visible ─────────────────
        const connectBtn = page.getByRole('button', {
            name: 'Connect JMAP account',
        })
        await expect(connectBtn).toBeVisible({ timeout: 8_000 })

        // ── 3. Click → OAuth redirect ──────────────────────────────────────────
        // The LemonLDAP-NG session cookie is already set in the browser context
        // via Playwright storageState, so the IDP redirects back without showing
        // a login prompt.
        const authorizeUrlPattern =
            /auth\.twake-dev\.maudet\.cloud\/oauth2\/authorize/
        const callbackUrlPattern =
            /\/sentinels\/mail\?tab=auth&status=connected/

        // Start navigation; Playwright follows the redirect chain automatically.
        await Promise.all([
            page.waitForURL(authorizeUrlPattern, { timeout: 15_000 }),
            connectBtn.click(),
        ])

        // ── 4. Wait for callback redirect back to the app ─────────────────────
        await page.waitForURL(callbackUrlPattern, { timeout: 30_000 })

        // ── 5. Assert connected state ─────────────────────────────────────────
        // The Auth tab shows "Connected as <email>" or the account_email text.
        await expect(
            page.getByText(/connected|Connected as/i),
        ).toBeVisible({ timeout: 8_000 })
    },
)
