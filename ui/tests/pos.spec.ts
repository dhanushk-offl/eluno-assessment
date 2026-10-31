import { test, expect } from '@playwright/test';

test('tablet sale, full refund and Shopify day-end', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  await expect(page.locator('.store-switch strong')).toHaveText('HSR', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Start a new sale' }).click();
  await expect(page.locator('#phone-country')).toContainText('+91');
  await page.getByLabel('Phone number').fill('9000000005');
  const lookup = page.waitForRequest(request => request.url().includes('/api/customers/lookup?') && request.method() === 'GET');
  await page.getByRole('button', { name: 'Find customer' }).click();
  expect(new URL((await lookup).url()).searchParams.get('phone')).toBe('+919000000005');
  await expect(page.getByRole('heading', { name: 'Karthik Reddy' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue to frame' }).click();
  await page.getByLabel('Search frames').fill('FR-0003');
  await page.getByRole('button', { name: /FR-0003/ }).click();
  await page.getByRole('button', { name: /LN-0043/ }).click();
  await expect(page.getByRole('heading', { name: 'Review the details' })).toBeVisible();
  const avatar = page.locator('.review-customer .avatar');
  await expect(avatar).toHaveText('K');
  const avatarBox = await avatar.boundingBox();
  expect(avatarBox!.width).toBeCloseTo(avatarBox!.height, 0);
  await expect(page.getByText('Offers, taken care of')).toBeVisible();
  await page.getByRole('button', { name: 'Continue to payment' }).click();
  await page.getByRole('button', { name: /^Charge/ }).click();
  await expect(page.getByRole('heading', { name: 'Sale completed.' })).toBeVisible({ timeout: 30_000 });
  const invoice = (await page.locator('.receipt-box strong').first().textContent())?.trim();
  expect(invoice).toBeTruthy();
  await page.getByRole('button', { name: 'View receipt' }).click();
  const receipt = page.getByRole('dialog', { name: 'Receipt & invoice' });
  await expect(receipt.getByText('Eluno HSR Layout')).toBeVisible();
  await expect(receipt.getByText(invoice!, { exact: true })).toBeVisible();
  await expect(receipt.getByText('Karthik Reddy')).toBeVisible();
  await expect(receipt.getByText(/Payment reference: chg_/)).toBeVisible();
  await expect(receipt.getByText(/^Order ID: /)).toBeVisible();
  await expect(receipt.getByText(/Shopify order ID/i)).toHaveCount(0);
  await expect(receipt.getByAltText(/QR code containing/)).toHaveAttribute('src', /^data:image\/png;base64,/);
  await page.evaluate(() => { (window as any).__printed = false; window.print = () => { (window as any).__printed = true; }; });
  await receipt.getByRole('button', { name: 'Print', exact: true }).click();
  expect(await page.evaluate(() => (window as any).__printed)).toBe(true);
  const downloadPromise = page.waitForEvent('download');
  await receipt.getByRole('button', { name: 'Download PDF' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  const file = await download.path();
  const { readFile } = await import('node:fs/promises');
  expect((await readFile(file!)).subarray(0, 5).toString()).toBe('%PDF-');
  await receipt.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('button', { name: 'View orders' }).click();
  await page.getByLabel('Search orders').fill(invoice!);
  await page.getByRole('button', { name: new RegExp(invoice!) }).click();
  await page.getByRole('button', { name: 'Process full refund' }).click();
  await page.getByRole('button', { name: 'Confirm full refund' }).click();
  await expect(page.getByText(/Credit note ELU/)).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Day-end' }).click();
  await expect(page.getByRole('heading', { name: 'Day-end report' })).toBeVisible();
  await expect(page.getByText('Source: Shopify')).toBeVisible();
  expect(errors).toEqual([]);
});

test('portrait tablet remains usable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Good to see you.' })).toBeVisible();
  await expect(page.locator('.store-switch strong')).toHaveText('HSR', { timeout: 30_000 });
  const sizes = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, content: document.documentElement.scrollWidth }));
  expect(sizes.content).toBeLessThanOrEqual(sizes.viewport);
  await page.getByRole('button', { name: 'Start a new sale' }).click();
  await expect(page.getByRole('heading', { name: 'Find your customer' })).toBeVisible();
  const saleSizes = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, content: document.documentElement.scrollWidth }));
  expect(saleSizes.content).toBeLessThanOrEqual(saleSizes.viewport);
});

test('sale reset protects a customer in progress', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.store-switch strong')).toHaveText('HSR', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Start a new sale' }).click();
  await page.getByLabel('Phone number').fill('9000000005');
  await page.getByRole('button', { name: 'Start over' }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a new sale?' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Keep current sale' }).click();
  await expect(page.getByLabel('Phone number')).toHaveValue('9000000005');
  await page.getByRole('button', { name: 'Start over' }).click();
  await dialog.getByRole('button', { name: 'Start new sale' }).click();
  await expect(page.getByLabel('Phone number')).toHaveValue('');
});

test('tablet navigation and sale summary remain accessible', async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto('/');
  await expect(page.locator('.store-switch strong')).toHaveText('HSR', { timeout: 30_000 });
  await expect(page.getByRole('button', { name: 'Orders & returns' }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Start a new sale' }).click();
  await expect(page.getByRole('navigation', { name: 'Sale progress' }).locator('[aria-current="step"]')).toContainText('Customer');
  await expect(page.getByRole('link', { name: 'View sale summary' })).toBeVisible();
  const order = await page.evaluate(() => ({ task: document.querySelector('.work-panel')!.getBoundingClientRect().top, summary: document.querySelector('.bill-panel')!.getBoundingClientRect().top }));
  expect(order.task).toBeLessThan(order.summary);
});

test('lost payment and refund responses recover the original operations', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.store-switch strong')).toHaveText('HSR', { timeout: 30_000 });
  await page.getByRole('button', { name: 'Start a new sale' }).click();
  await page.getByLabel('Phone number').fill('9000000005');
  await page.getByRole('button', { name: 'Find customer' }).click();
  await page.getByRole('button', { name: 'Continue to frame' }).click();
  await page.getByLabel('Search frames').fill('FR-0003');
  await page.getByRole('button', { name: /FR-0003/ }).click();
  await page.getByRole('button', { name: /LN-0043/ }).click();
  await page.getByRole('button', { name: 'Continue to payment' }).click();
  let checkoutWrites = 0;
  await page.route('**/api/checkouts', async route => {
    checkoutWrites++;
    await route.fetch(); // Commit the sale, then simulate the response being lost.
    await route.fulfill({ status: 503, json: { error: 'service_unavailable', message: 'Connection lost' } });
  });
  await page.getByRole('button', { name: /^Charge/ }).click();
  await expect(page.getByRole('button', { name: 'Check status' })).toBeVisible();
  await page.getByRole('button', { name: 'Check status' }).click();
  await expect(page.getByRole('heading', { name: 'Sale completed.' })).toBeVisible();
  expect(checkoutWrites).toBe(1);
  const invoice = (await page.locator('.receipt-box strong').first().textContent())!.trim();
  await page.getByRole('button', { name: 'View orders' }).click();
  await page.getByLabel('Search orders').fill(invoice);
  await page.getByRole('button', { name: new RegExp(invoice) }).click();
  let refundWrites = 0;
  await page.route('**/api/refunds', async route => {
    refundWrites++;
    await route.fetch(); // Commit the refund, then lose its response.
    await route.fulfill({ status: 503, json: { error: 'service_unavailable', message: 'Connection lost' } });
  });
  await page.getByRole('button', { name: 'Process full refund' }).click();
  await page.getByRole('button', { name: 'Confirm full refund' }).click();
  await expect(page.getByRole('button', { name: 'View status' })).toBeVisible();
  await page.getByRole('button', { name: 'View status' }).click();
  await expect(page.getByText(/Credit note ELU/)).toBeVisible();
  expect(refundWrites).toBe(1);
});
