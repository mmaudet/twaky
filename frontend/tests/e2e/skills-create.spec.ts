import { test, expect, setMonacoValue } from './fixtures'

test('create skill via UI, verify it appears in list, cleanup via delete', async ({ signedInPage: page }) => {
    // Navigate to /skills
    await page.goto('/skills')
    await expect(page.getByRole('heading', { name: 'Skills' })).toBeVisible()

    // Click "+ New skill" — navigates to /skills/new
    await page.getByRole('link', { name: '+ New skill' }).click()
    await expect(page).toHaveURL(/\/skills\/new$/)

    // Fill Name
    await page.getByLabel(/^name$/i).fill('e2e_echo')

    // Fill Description — identified by htmlFor="desc" → label text "Description"
    await page.getByLabel(/^description$/i).fill('E2E echo skill')

    // Paste rather than type: Monaco auto-closes the quotes in `return "hello"`
    // and auto-indents the continuation line, so typed source arrives mangled.
    await setMonacoValue(page, 'def run(**kwargs):\n    return "hello"')

    // Bind Atlas via the labeled checkbox (id="bind-atlas")
    await page.getByLabel('atlas', { exact: true }).click()

    // Click Create button
    await page.getByRole('button', { name: /^create$/i }).click()

    // Redirect to /skills/<uuid>
    await expect(page).toHaveURL(/\/skills\/[0-9a-f-]{36}$/)

    // Navigate back to list — row for e2e_echo must be visible
    await page.goto('/skills')
    await expect(page.getByText('e2e_echo')).toBeVisible()

    // Cleanup: delete the row so the test is idempotent
    // Click Delete button in the e2e_echo row (AlertDialogTrigger)
    const echoRow = page.getByRole('row').filter({ hasText: 'e2e_echo' })
    await echoRow.getByRole('button', { name: /^delete$/i }).click()
    // AlertDialog confirm button — also labelled "Delete"; use last() since the trigger is hidden
    await page.getByRole('button', { name: /^delete$/i }).last().click()

    // Row must disappear from the list. Asserted on the row, not the page: the
    // deletion toast reads "Skill 'e2e_echo' deleted", so a page-wide match
    // finds two elements and fails on strict mode whenever the toast is still
    // up — a timing difference between a laptop and a CI runner.
    await expect(
        page.getByRole('row').filter({ hasText: 'e2e_echo' }),
    ).toHaveCount(0, { timeout: 5000 })
})
