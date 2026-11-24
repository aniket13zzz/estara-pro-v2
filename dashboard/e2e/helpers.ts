import { expect, type Page } from "@playwright/test";
import path from "node:path";

export const adminEmail = process.env.E2E_ADMIN_EMAIL ?? "admin@example.com";
export const adminPassword = process.env.E2E_ADMIN_PASSWORD ?? "";
export const staffEmail = process.env.E2E_STAFF_EMAIL ?? "staff@example.com";
export const staffPassword = process.env.E2E_STAFF_PASSWORD ?? "";

export const SAMPLE_DIR = path.resolve(__dirname, "../../backend/tests/fixtures/sample_inventory");
export const SAMPLE_FILES = [
  "projects",
  "unit_types",
  "units",
  "price_lists",
  "offers",
  "payment_plans",
  "amenities",
];

export async function signIn(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/home$/);
}

export async function uploadFile(page: Page, file: string | { name: string; buffer: Buffer }) {
  await page.goto("/data-center");
  const input = page.locator("#file");
  if (typeof file === "string") {
    await input.setInputFiles(file);
  } else {
    await input.setInputFiles({ name: file.name, mimeType: "text/csv", buffer: file.buffer });
  }
  await page.getByRole("button", { name: "Upload and check" }).click();
  await expect(page).toHaveURL(/\/data-center\/batches\//);
}
