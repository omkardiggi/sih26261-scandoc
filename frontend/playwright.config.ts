import { defineConfig } from "@playwright/test";

// Runs against a server started on an isolated e2e database (see docs/TESTING.md).
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  use: { baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8765", channel: "chrome", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
  ],
});
