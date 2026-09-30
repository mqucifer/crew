const { test, expect } = require("@playwright/test");
const path = require("path");

test("the health summary is the first section", async ({ page }) => {
  await page.goto("file://" + path.join(__dirname, "..", "index.html"));
  await expect(page.locator("main > section").first()).toHaveAttribute("id", "health");
  await expect(page.locator("#health p")).toHaveText("Status: All clear");
});
