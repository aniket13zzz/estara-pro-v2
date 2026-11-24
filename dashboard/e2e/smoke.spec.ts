import { expect, test } from "@playwright/test";
import { adminEmail, adminPassword, signIn } from "./helpers";

// Needs the full stack running with the sample users seeded (`make up && make migrate &&
// make seed-sample`). Credentials come from the environment.

test("status page shows backend health", async ({ page }) => {
  await page.goto("/health");
  await expect(page.getByTestId("overall")).toHaveText("All systems OK");
  await expect(page.getByText("en, hi")).toBeVisible();
});

test("wrong password shows an error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(adminEmail);
  await page.getByLabel("Password").fill("definitely-not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  // Scoped to the form: Next.js renders its own role="alert" route announcer.
  await expect(page.locator("form").getByRole("alert")).toContainText("Wrong email or password");
});

test("admin can sign in and lands on the home page", async ({ page }) => {
  test.skip(!adminPassword, "Set E2E_ADMIN_PASSWORD to the seeded admin password");
  await signIn(page, adminEmail, adminPassword);
  await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pause the agent" })).toBeVisible();
  await expect(page.getByTestId("whoami")).toContainText("Admin");
});
