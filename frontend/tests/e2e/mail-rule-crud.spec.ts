import { test, expect, setMonacoValue } from './fixtures'
import type { Page } from '@playwright/test'

/**
 * Commit the rule currently in the editor.
 *
 * The editor gained a mandatory review step: "Preview matches" runs the
 * proposal, a checkbox acknowledges it, and only then does "Apply rule"
 * appear and enable. The single "Save rule" button this spec used to click
 * no longer exists.
 */
async function applyRule(page: Page): Promise<void> {
    await page.getByRole('button', { name: 'Preview matches' }).click()
    await page.getByLabel('I have reviewed the matches').check()
    await page.getByRole('button', { name: 'Apply rule' }).click()
}

// Unique suffix to avoid collisions between parallel test runs
const SUFFIX = Date.now()
const RULE_NAME = `e2e_test_rule_${SUFFIX}`
const RULE_NAME_EDITED = `e2e_test_rule_edited_${SUFFIX}`

const newRuleJson = JSON.stringify(
    {
        name: RULE_NAME,
        description: 'Created by Playwright E2E test',
        conditions: [{ field: 'from', operator: 'contains', value: '@e2e-test.invalid' }],
        combinator: 'OR',
        actions: ['archive'],
        priority: 99,
        enabled: true,
        run_on_threads: false,
    },
    null,
    2,
)

const editedRuleJson = JSON.stringify(
    {
        name: RULE_NAME_EDITED,
        description: 'Edited by Playwright E2E test',
        conditions: [{ field: 'subject', operator: 'contains', value: '[E2E]' }],
        combinator: 'OR',
        actions: ['archive'],
        priority: 98,
        enabled: true,
        run_on_threads: false,
    },
    null,
    2,
)

test('mail-rule-crud: create, verify in list, edit, delete', async ({ signedInPage: page }) => {
    // ── Step 1: Navigate to the Mail Sentinel page ────────────────────────
    await page.goto('/sentinels/mail')
    await expect(page.getByRole('heading', { name: 'Mail Sentinel' })).toBeVisible()

    // Ensure the Rules tab is active (it is the default)
    await page.getByRole('tab', { name: 'Rules' }).click()

    // ── Step 2: Create a new rule ──────────────────────────────────────────
    await page.getByRole('link', { name: '+ New rule' }).click()
    await expect(page).toHaveURL(/\/sentinels\/mail\/rules\/new$/)
    await expect(page.getByRole('heading', { name: 'New rule' })).toBeVisible()

    // Replace the entire Monaco editor content with our test rule JSON.
    await setMonacoValue(page, newRuleJson)

    // Commit the new rule — response redirects back to /sentinels/mail
    await applyRule(page)
    await expect(page).toHaveURL(/\/sentinels\/mail$/, { timeout: 10000 })

    // ── Step 3: Verify the new rule appears in the list ────────────────────
    await expect(page.getByRole('tab', { name: 'Rules' })).toBeVisible()
    await expect(page.getByText(RULE_NAME)).toBeVisible()

    // ── Step 4: Edit the rule ──────────────────────────────────────────────
    const ruleRow = page.getByRole('row').filter({ hasText: RULE_NAME })
    await ruleRow.getByRole('link', { name: 'Edit' }).click()
    await expect(page).toHaveURL(/\/sentinels\/mail\/rules\/[0-9a-f-]{36}$/)

    // Replace JSON content with the edited version
    await setMonacoValue(page, editedRuleJson)

    await applyRule(page)
    // Editing patches in place and deliberately keeps you on the rule page —
    // only creation redirects. Confirm the save, then go back to the list.
    await expect(page.getByText('Rule saved')).toBeVisible({ timeout: 10000 })
    await page.goto('/sentinels/mail')

    // Verify the edited name appears and the old name is gone
    await expect(page.getByText(RULE_NAME_EDITED)).toBeVisible()
    await expect(page.getByText(RULE_NAME)).not.toBeVisible()

    // ── Step 5: Delete the rule ────────────────────────────────────────────
    const editedRow = page.getByRole('row').filter({ hasText: RULE_NAME_EDITED })
    await editedRow.getByRole('button', { name: /^Delete$/ }).click()

    // Confirm in the AlertDialog
    await page.getByRole('button', { name: /^Delete$/ }).last().click()

    // Rule must disappear
    await expect(page.getByText(RULE_NAME_EDITED)).not.toBeVisible({ timeout: 5000 })
})
