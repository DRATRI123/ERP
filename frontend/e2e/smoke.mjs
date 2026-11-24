/**
 * End-to-end smoke test of the real UI against a running stack (frontend on :3000 proxying the
 * API). Drives a complete counter sale with the keyboard flow and checks the result through the
 * API: invoice finalized and paid, stock reduced, books balanced.
 *
 *   ERP_E2E_EMAIL=… ERP_E2E_PASSWORD=… node e2e/smoke.mjs
 *
 * Needs Playwright with a Chromium (npm i -D playwright && npx playwright install chromium).
 */
import { createRequire } from "node:module";

// Works with a local install or a global one found through NODE_PATH.
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.env.ERP_E2E_URL ?? "http://localhost:3000";
const EMAIL = process.env.ERP_E2E_EMAIL;
const PASSWORD = process.env.ERP_E2E_PASSWORD;
if (!EMAIL || !PASSWORD) throw new Error("Set ERP_E2E_EMAIL and ERP_E2E_PASSWORD");

const fail = (m) => { console.error(`FAIL: ${m}`); process.exitCode = 1; };
const api = async (path, token) => (await fetch(`${BASE}/api/v1${path}`, { headers: { Authorization: `Bearer ${token}` } })).json();

const browser = await chromium.launch(process.env.ERP_E2E_CHROMIUM ? { executablePath: process.env.ERP_E2E_CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("response", (r) => { if (r.url().includes("/api/v1/") && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`); });
let token = null;
page.on("response", async (r) => { if (r.url().endsWith("/auth/login") && r.ok()) token = (await r.json()).access_token; });

// 1. Sign in
await page.goto(`${BASE}/login`);
await page.waitForLoadState("networkidle");
await page.fill("#email", EMAIL);
await page.fill("#password", PASSWORD);
await page.click("button[type=submit]");
await page.waitForURL("**/dashboard", { timeout: 30_000 });
console.log("✓ signed in");

const me = await api("/auth/me", token);
const biz = me.businesses[0].business_id;
const product = (await api(`/businesses/${biz}/products?limit=1`, token)).items[0];
const before = Number(product.on_hand);

// 2. Counter sale: search the product, add it twice (quantity merges), pay by UPI, F9
await page.goto(`${BASE}/sales/new`);
await page.waitForLoadState("networkidle");
const search = page.getByLabel("Search products");
for (let i = 0; i < 2; i++) {
  await search.fill(product.sku);
  await page.getByRole("listbox").getByRole("option").first().waitFor();
  await search.press("Enter");
  await page.waitForTimeout(300);
}
await page.getByLabel("Quantity").first().waitFor();
if ((await page.getByLabel("Quantity").first().inputValue()) !== "2") fail("adding the same product twice did not merge into quantity 2");
await page.waitForFunction(() => !document.body.innerText.includes("₹0.00\n") && /Total\s*₹[1-9]/.test(document.body.innerText), null, { timeout: 15_000 });
await page.getByRole("button", { name: "UPI" }).click();
await page.keyboard.press("F9");
await page.waitForURL(/\/documents\/[0-9a-f-]{36}/, { timeout: 30_000 });
const docId = page.url().match(/documents\/([0-9a-f-]{36})/)[1];
await page.getByRole("dialog").getByText("Print preview").waitFor({ timeout: 15_000 });
console.log("✓ invoice finalized; print preview opened");

// 3. Verify through the API
const doc = await api(`/businesses/${biz}/documents/${docId}`, token);
if (doc.status !== "final" || doc.payment_status !== "paid") fail(`invoice ${doc.number} is ${doc.status}/${doc.payment_status}`);
const after = Number((await api(`/businesses/${biz}/products/${product.id}`, token)).on_hand);
if (before - after !== 2) fail(`stock went from ${before} to ${after}, expected -2`);
const integrity = await api(`/businesses/${biz}/accounting/reports/integrity`, token);
if (!integrity.ok) fail(`books integrity: ${JSON.stringify(integrity)}`);
console.log(`✓ ${doc.number}: paid, stock ${before} → ${after}, books balanced`);

// 4. Global search finds the new invoice
await page.keyboard.press("Escape");
await page.keyboard.press("Control+k");
await page.getByPlaceholder(/Search products, customers/).fill(doc.number.slice(-4));
await page.getByText(doc.number).first().waitFor({ timeout: 10_000 });
console.log("✓ global search finds it");

if (errors.length) fail(errors.join("\n"));
await browser.close();
if (!process.exitCode) console.log("E2E smoke test passed");
