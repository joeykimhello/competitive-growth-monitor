import { test } from '@playwright/test';

test('print page title', async ({ page }) => {
  const url = process.env.TARGET_URL;
  if (!url) throw new Error('TARGET_URL is not set');

  await page.goto(url);
  const title = await page.title();
  console.log(`Page title: ${title}`);
});
