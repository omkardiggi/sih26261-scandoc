import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_EMAIL || "e2e.officer@example.test";
const PASSWORD = process.env.E2E_PASSWORD || "Quartz-Lantern-2026!";
const SECTIONS = ["Dashboard", "Institutions", "Institution Verifier", "Inspection & Analysis", "Reports & Audit"];

async function login(page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

test("landing page is public and protected routes redirect", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Verify institutions");
  await page.goto("/app/institutions");
  await expect(page).toHaveURL(/\/login/);
});

test("anyone can sign up with their own email and is signed in", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Sign up" }).click();
  await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
  await page.getByLabel("Full name").fill("Synthetic Signup Tester");
  await page.getByLabel("Email").fill(`synthetic.signup.${Date.now()}@gmail.com`);
  await page.getByLabel("Password", { exact: true }).fill("Quartz-Lantern-2026!");
  await page.getByLabel("Repeat password").fill("Quartz-Lantern-2026!");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
});

test("officer can sign in, use all five sections, and sign out", async ({ page, isMobile }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  const openNav = async () => { if (isMobile && !(await page.getByRole("link", { name: "Reports & Audit", exact: true }).isVisible())) await page.getByRole("button", { name: "Open navigation" }).click(); };
  await openNav();
  const nav = page.getByRole("navigation").first();
  await expect(nav.getByRole("link")).toHaveCount(5);
  for (const name of SECTIONS) {
    await openNav().catch(() => {});
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${name} horizontal overflow`).toBeLessThanOrEqual(1);
  }
  // Create an institution (synthetic, e2e database only) and upload a synthetic PDF
  if (info.project.name === "desktop") {
    await openNav().catch(() => {});
    await page.getByRole("link", { name: "Institutions", exact: true }).click();
    await page.getByRole("button", { name: "Add institution" }).first().click();
    const unique = `E2E Synthetic Institute ${Date.now()}`;
    await page.getByLabel("Institution name").fill(unique);
    await page.locator("#f-state").fill("Testland");
    await page.getByRole("dialog").getByRole("button", { name: "Add institution" }).click();
    await expect(page.getByRole("heading", { level: 1, name: unique })).toBeVisible();
    await expect(page.getByText("No documents uploaded yet")).toBeVisible();
    const pdf = Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Contents 4 0 R/Resources<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>>>>>>>endobj 4 0 obj<</Length 120>>stream\nBT /F1 14 Tf 72 760 Td (SYNTHETIC TEST DOCUMENT - Fire Safety Certificate) Tj 0 -24 Td (Valid up to 31-12-2030) Tj ET\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF");
    await page.locator(".dropzone input[type=file]").setInputFiles({ name: "synthetic.pdf", mimeType: "application/pdf", buffer: pdf });
    const row = page.locator("table.matrix tbody tr", { hasText: "synthetic.pdf" });
    await expect(row).toBeVisible({ timeout: 30000 });
    await expect(page.getByText("Final decision")).toBeVisible();
    await row.click();
    await expect(page.getByText("The document, annotated")).toBeVisible({ timeout: 90000 });
    await expect(page.locator(".rp-page")).toHaveCount(1);
    await expect(page.getByRole("img", { name: /Page 1 of the original document/ })).toBeVisible();
    await expect(page.getByText("Found on this page")).toBeVisible();
    await expect(page.getByText(/All checks performed/)).toBeVisible();
    await expect(page.locator("iframe")).toHaveCount(0);
    await expect(page.getByText("Unable to verify against official records.")).toHaveCount(0);
  }
  await page.getByRole("button", { name: /Automated Tester/ }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  expect(errors).toEqual([]);
});
