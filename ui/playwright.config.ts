import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: process.env.POS_UI_URL || 'http://127.0.0.1:5173',
    browserName: 'firefox',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_FIREFOX || undefined },
    viewport: { width: 1180, height: 820 },
    trace: 'retain-on-failure'
  }
});
