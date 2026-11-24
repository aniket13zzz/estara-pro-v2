import { expect, test } from "@playwright/test";
import path from "node:path";
import {
  adminEmail,
  adminPassword,
  SAMPLE_DIR,
  SAMPLE_FILES,
  signIn,
  staffEmail,
  staffPassword,
  uploadFile,
} from "./helpers";

// Runs against the live stack. Re-uploading the sample inventory is safe: unchanged rows become
// new approved versions (and refresh freshness).

test.describe.configure({ mode: "serial" });

test("admin imports the sample inventory end to end through the dashboard", async ({ page }) => {
  test.skip(!adminPassword, "Set E2E_ADMIN_PASSWORD");
  await signIn(page, adminEmail, adminPassword);
  for (const name of SAMPLE_FILES) {
    await uploadFile(page, path.join(SAMPLE_DIR, `${name}.csv`));
    await expect(page.getByRole("heading", { level: 1 })).toContainText(`${name}.csv`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("awaiting approval");
    await page.getByRole("button", { name: "Approve" }).click();
    await expect(page.getByText("Approved. The agent now uses this data.")).toBeVisible();
  }

  await page.getByRole("link", { name: "Properties" }).click();
  const greens = page.getByRole("region", { name: "Sunrise Greens (Sample)" });
  await expect(greens).toBeVisible();
  const types = greens.getByRole("table", { name: "SG unit types" });
  await expect(types.getByRole("row", { name: /2 BHK Classic/ })).toContainText("₹72,00,000 – ₹78,00,000");
  await expect(greens).toContainText("possession December 2027");
});

test("a file with errors shows them and cannot be approved", async ({ page }) => {
  test.skip(!adminPassword, "Set E2E_ADMIN_PASSWORD");
  await signIn(page, adminEmail, adminPassword);
  const bad = [
    "project_code,unit_number,unit_type_code,floor,bhk,property_type,carpet_area_sqft,price_inr,availability",
    "SG,E2E-1,,3,2,apartment,820,0,available",
    "ZZ,E2E-2,,3,2,apartment,820,7200000,available",
  ].join("\n");
  await uploadFile(page, { name: "units.csv", buffer: Buffer.from(bad) });
  await expect(page.getByRole("heading", { level: 1 })).toContainText("failed");
  const errors = page.getByRole("table", { name: "Validation errors" });
  await expect(errors).toContainText("Amounts must be greater than 0.");
  await expect(errors).toContainText("ZZ does not exist");
  await expect(page.getByRole("button", { name: "Approve" })).toBeDisabled();
});

test("sales staff can review uploads but not approve them", async ({ page }) => {
  test.skip(!staffPassword, "Set E2E_STAFF_PASSWORD");
  await signIn(page, staffEmail, staffPassword);
  await uploadFile(page, path.join(SAMPLE_DIR, "amenities.csv"));
  await expect(page.getByText("A client admin must approve this upload.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
});

test("freshness and uploads lists load", async ({ page }) => {
  test.skip(!adminPassword, "Set E2E_ADMIN_PASSWORD");
  await signIn(page, adminEmail, adminPassword);
  await page.goto("/data-center");
  const freshness = page.getByRole("table", { name: "Data freshness" });
  await expect(freshness.getByRole("row", { name: /^Units/ })).toBeVisible();
  await expect(page.getByRole("table", { name: "Uploads" })).toContainText("amenities.csv");
  await expect(page.getByRole("link", { name: "Excel workbook (all sheets + README)" })).toBeVisible();
});
