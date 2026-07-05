const path = require("path");
const fs = require("fs");
const { chromium } = require("@playwright/test");

const FRONTEND_BASE = "http://127.0.0.1:3000";
const OUTPUT_DIR = path.resolve(__dirname, "..", "..", "..", "rapport", "moones", "images");

const ADMIN_EMAIL = "admin.master@example.com";
const ADMIN_PASSWORD = "AdminPass123!";

function ensureOutputDir() {
  if (!fs.existsSync(OUTPUT_DIR)) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }
}

async function screenshotLogin(page) {
  await page.goto(`${FRONTEND_BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[placeholder="Email"]', "learner.one@adaptiq.local");
  await page.fill('input[placeholder="Password"]', "************");
  await page.screenshot({
    path: path.join(OUTPUT_DIR, "login_page.jpg"),
    type: "jpeg",
    quality: 92,
  });
  await page.fill('input[placeholder="Email"]', ADMIN_EMAIL);
  await page.fill('input[placeholder="Password"]', ADMIN_PASSWORD);
}

async function loginAndReachDashboard(page) {
  await Promise.all([
    page.waitForURL("**/dashboard", { timeout: 25_000 }),
    page.getByRole("button", { name: /log in/i }).click(),
  ]);
  await page.waitForTimeout(2500);
}

async function screenshotAdmin(page) {
  await page.goto(`${FRONTEND_BASE}/admin`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  await page.screenshot({
    path: path.join(OUTPUT_DIR, "admin_dashboard.jpg"),
    type: "jpeg",
    quality: 92,
  });
}

async function screenshotClassicWithHint(page) {
  await page.goto(`${FRONTEND_BASE}/rooms/classic`, { waitUntil: "networkidle" });
  await page.locator('button:has-text("History")').first().click();
  await page.getByText(/question\s+1\s*\/\s*10/i).waitFor({ timeout: 25_000 });
  await page.waitForTimeout(800);

  const hintButton = page.getByRole("button", { name: /request hint/i });
  await hintButton.waitFor({ timeout: 12_000 });
  await hintButton.click();

  await page.getByText(/archival hint/i).waitFor({ timeout: 12_000 });
  await page.waitForTimeout(600);

  await page.screenshot({
    path: path.join(OUTPUT_DIR, "classic_room.jpg"),
    type: "jpeg",
    quality: 92,
  });
}

async function screenshotScholarChat(page) {
  await page.goto(`${FRONTEND_BASE}/dashboard`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);

  await page.keyboard.press("Alt+S");
  await page.getByRole("heading", { name: "The Scholar" }).first().waitFor({ timeout: 10_000 });

  const quickPrompt = page.getByRole("button", { name: /what caused wwi\?/i }).last();
  await quickPrompt.waitFor({ timeout: 10_000 });
  await quickPrompt.click();

  await page.getByText(/imposed reparations on Germany/i).last().waitFor({ timeout: 15_000 });
  await page.waitForTimeout(1200);

  await page.screenshot({
    path: path.join(OUTPUT_DIR, "scholar_chat.jpg"),
    type: "jpeg",
    quality: 92,
  });
}

async function main() {
  ensureOutputDir();

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1536, height: 1060 },
  });

  // Keep hint/chat screenshots deterministic and fast.
  await context.route("**/api/rooms/classic/hints", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        hint: "Focus on the political settlement that followed World War I and redrew European boundaries.",
      }),
    });
  });

  await context.route("**/api/chat/ask", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        answer:
          "The Treaty of Versailles imposed reparations on Germany, reduced its military power, reshaped European borders, and left unresolved tensions that later contributed to World War II.",
        sources: ["Wikipedia", "Wikidata"],
        topic: "history",
        grounded: true,
      }),
    });
  });

  const page = await context.newPage();
  try {
    await screenshotLogin(page);
    await loginAndReachDashboard(page);
    await screenshotAdmin(page);
    await screenshotClassicWithHint(page);
    await screenshotScholarChat(page);
    console.log(`Screenshots updated in: ${OUTPUT_DIR}`);
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
