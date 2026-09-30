// A static page opened from disk: no server, and no network while the test runs.
const { defineConfig } = require("@playwright/test");
module.exports = defineConfig({
  testDir: "tests",
  reporter: "line",
  use: { headless: true },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
