import { test as base, expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'

async function stackReachable(baseURL: string): Promise<boolean> {
    try {
        const res = await fetch(`${baseURL}/api/healthz`)
        return res.ok
    } catch {
        return false
    }
}

// The instance owner require_owner() checks against. Hardcoding a real
// address pins the suite to one deployment: CI provisions its own owner
// (scripts/ci-env.sh) and every authenticated request came back 403.
export const OWNER_EMAIL =
    process.env.TWAKY_OWNER_EMAIL || 'michel.maudet@linagora.com'

function forgeSessionCookie(email: string): string {
    // Uses twaky.api.testing, the seam that module exists to provide, rather
    // than scripts/sign-session.py: the Dockerfile copies only src/, so that
    // script is not in the image. And plain `python`, not `uv run` — the image
    // already puts /app/.venv/bin on PATH, while uv wants a writable cache
    // under $HOME, which is /nonexistent for the `nobody` user it runs as.
    try {
        return execFileSync(
            'docker',
            ['compose', 'exec', '-T', 'twaky-api', 'python', '-c',
             'import sys; from twaky.api.testing import sign_session; print(sign_session(sys.argv[1]))',
             email],
            { cwd: process.cwd() + '/..' },
        ).toString().trim()
    } catch (err) {
        throw new Error(
            'Could not forge a session cookie. Is the docker stack up? ' +
            `Underlying error: ${(err as Error).message}`,
        )
    }
}

// Extend `page` to self-skip when the Twaky stack is not reachable.
// Both unauthenticated and authenticated tests use this base fixture.
export const test = base.extend<{ signedInPage: Page }>({
    page: async ({ page, baseURL }, use) => {
        if (!process.env.TWAKY_TEST_STACK_URL || !(await stackReachable(baseURL!))) {
            test.skip(true, 'twaky stack not reachable — set TWAKY_TEST_STACK_URL')
        }
        // eslint-disable-next-line react-hooks/rules-of-hooks -- Playwright fixture `use`, not a React hook
        await use(page)
    },

    signedInPage: async ({ page, context, baseURL }, use) => {
        // Stack reachability already guaranteed by the `page` fixture above.
        const cookie = forgeSessionCookie(OWNER_EMAIL)
        const domain = new URL(baseURL!).hostname
        await context.addCookies([{
            name: 'twaky_session', value: cookie,
            domain, path: '/', httpOnly: true, secure: false,
        }])
        // eslint-disable-next-line react-hooks/rules-of-hooks -- Playwright fixture `use`, not a React hook
        await use(page)
    },
})

/**
 * Replace the content of the page's Monaco editor.
 *
 * Do not type source into Monaco with `keyboard.type`: its auto-closing
 * brackets and quotes fight the input. An opening `{` inserts a `}` ahead of
 * the cursor, the typed `}` lands after it, and the document ends up
 * syntactically broken — the rule editor showed "validation errors" and never
 * enabled Save. Setting the model value through Monaco's own API is
 * deterministic, and still fires the change event the React wrapper listens to.
 */
/**
 * Replace the content of the page's Monaco editor, via a real clipboard paste.
 *
 * Three approaches fail here, each silently producing a wrong submission
 * rather than an error:
 *
 * 1. `keyboard.type` fights Monaco's input aids — an opening `{` inserts a
 *    matching `}` ahead of the cursor, the typed `}` lands after it, and the
 *    document ends up syntactically broken.
 * 2. Disabling those aids with `updateOptions` does not hold: the editor's
 *    `options` prop is an inline object literal, so every re-render (i.e. every
 *    keystroke) re-applies it and wipes the overrides.
 * 3. Setting the model value through Monaco's API avoids typing altogether,
 *    but @monaco-editor/react does not surface programmatic edits to its
 *    onChange: the editor displayed the new content while React kept the
 *    default, and the form submitted the default.
 *
 * A paste is verbatim (auto-closing does not apply) and is a real input event,
 * so React sees it.
 */
export async function setMonacoValue(page: Page, value: string): Promise<void> {
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.locator('.monaco-editor').first().click()
    await page.evaluate((v) => navigator.clipboard.writeText(v), value)
    await page.keyboard.press('ControlOrMeta+A')
    await page.keyboard.press('ControlOrMeta+V')
}

export { expect }
