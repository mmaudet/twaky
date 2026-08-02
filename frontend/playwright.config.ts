import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.TWAKY_TEST_STACK_URL || 'http://localhost:3000'

export default defineConfig({
    testDir: './tests/e2e',
    fullyParallel: false,  // avoid mission-state race conditions
    forbidOnly: !!process.env.CI,
    retries: process.env.CI ? 1 : 0,
    reporter: process.env.CI ? [['github'], ['html']] : 'html',
    use: {
        baseURL,
        trace: 'on-first-retry',
    },
    projects: [
        { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    ],
})
