import { expect, test } from "@playwright/test";
import { signIn, staffEmail, staffPassword } from "./helpers";

// The sales pages load with real data or a clear empty state, never an error, and the test-call
// page explains what is missing until voice is set up.
test.describe("sales pages", () => {
  test.skip(!staffPassword, "set E2E_STAFF_PASSWORD (the seeded sales user's password)");

  test.beforeEach(async ({ page }) => {
    await signIn(page, staffEmail, staffPassword);
  });

  test("leads, visits and calls load without errors", async ({ page }) => {
    for (const [path, heading] of [
      ["/leads", "Leads"],
      ["/visits", "Site visits"],
      ["/calls", "Calls"],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      await expect(page.getByRole("status").filter({ hasText: "Loading" })).toHaveCount(0);
      // Scoped to the page content: Next.js renders its own role="alert" route announcer.
      await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
    }
  });

  test("a lead opens with its requirements, visits and conversation", async ({ page }) => {
    await page.goto("/leads");
    await expect(page.getByRole("status").filter({ hasText: "Loading" })).toHaveCount(0);
    const first = page.getByRole("table", { name: "Leads" }).getByRole("link").first();
    test.skip((await first.count()) === 0, "no leads in this database yet");
    await first.click();
    await expect(page.getByRole("heading", { name: "What they need" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Site visits" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Conversation" })).toBeVisible();
  });

  test("staff confirm a requested visit", async ({ page }) => {
    await page.goto("/visits");
    await expect(page.getByRole("status").filter({ hasText: "Loading" })).toHaveCount(0);
    const confirm = page.getByRole("button", { name: "Confirm" }).first();
    test.skip((await confirm.count()) === 0, "no visit waiting for confirmation");
    const card = page.locator("section.card", { has: confirm });
    const heading = await card.getByRole("heading").textContent();
    await confirm.click();
    const same = page.locator("section.card", { hasText: heading ?? "" });
    await expect(same.getByText("confirmed", { exact: true })).toBeVisible();
    await expect(same.getByRole("button", { name: "Visited" })).toBeVisible();
  });

  test("test call page says what is missing until voice is set up", async ({ page }) => {
    await page.goto("/test-call");
    await expect(page.getByRole("heading", { level: 1, name: "Test call" })).toBeVisible();
    const notReady = page.getByText("Voice is not set up yet.");
    const start = page.getByRole("button", { name: "Start call" });
    await expect(notReady.or(start)).toBeVisible();
  });
});

test.describe("home page", () => {
  test.skip(!staffPassword, "set E2E_STAFF_PASSWORD");

  test("sales staff see today's numbers but cannot pause the agent", async ({ page }) => {
    await signIn(page, staffEmail, staffPassword);
    await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Visit-booking rate" })).toBeVisible();
    await expect(page.getByText("Only an admin can pause or resume the agent.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause the agent" })).toHaveCount(0);
  });
});
