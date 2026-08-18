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

export { expect }
