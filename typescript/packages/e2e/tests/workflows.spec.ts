// The scientist's workflows end to end on the demonstration server. Each test has its own browser
// context, so its own copy of the synthetic data: buttons here never change another test's page.
import { expect, test } from "@playwright/test";

const LARGE = `/plans/${encodeURIComponent("sha256:297aea48cac6cf41832f6b5207e6cd04ed5034bb611d1c5d72b2a4232ffc1181")}`;
const SMALL = `/plans/${encodeURIComponent("sha256:54c1021fcaf5706dfd1d4023b6f8cca9315f961ca6bd1fe88537ece815b70a88")}`;

test("home lists what is waiting and where each piece of work is", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demonstration")).toContainText("synthetic");
  const waiting = page.getByTestId("waiting").locator("li");
  await expect(waiting).toHaveCount(5);
  await expect(waiting.filter({ hasText: "1.29 GB" })).toContainText("Needs a signed approval");
  await expect(page.getByTestId("work-experiments").locator("> li")).toHaveCount(4);
  const failed = page.getByTestId("work-experiments").locator("> li").filter({ hasText: "exp-000003" });
  await expect(failed.locator('[data-status="failed"]')).toHaveCount(1);
  await expect(page.getByTestId("work-sets")).toContainText("Review 3 candidates");
});

test("runs a planned experiment and checks it against its gates", async ({ page }) => {
  await page.goto("/experiments/exp-000004");
  await expect(page.getByTestId("no-verdict")).toBeVisible();
  await page.getByRole("button", { name: "Run it" }).click();
  await expect(page.getByTestId("action-done")).toContainText("now completed");
  await page.getByRole("button", { name: "Check against the gates" }).click();
  await expect(page.getByTestId("action-done")).toContainText("INCONCLUSIVE");
  await expect(page.getByTestId("verdict")).toContainText("Inconclusive");
  await expect(page.getByTestId("next-step")).toContainText("sdp dossier assemble exp-000004");
});

test("explores a field from the start page, and says what is wrong with the form", async ({ page }) => {
  await page.goto("/start");
  const form = page.getByTestId("field-form");
  await form.getByLabel("Right ascension").fill("400");
  await form.getByLabel("Declination").fill("-10");
  await form.getByLabel("Your name").fill("Ada");
  await page.getByRole("button", { name: "Explore without a plan" }).click();
  await expect(page.getByTestId("form-error")).toContainText("Right ascension is in degrees");
  await expect(form.getByLabel("Declination")).toHaveValue("-10");
  await form.getByLabel("Right ascension").fill("120");
  await page.getByRole("button", { name: "Explore without a plan" }).click();
  await expect(page).toHaveURL(/\/investigations\/invset-000001$/);
});

test("plans the downloads first, and remembers who you are", async ({ page }) => {
  await page.goto("/start");
  const form = page.getByTestId("field-form");
  await form.getByLabel("Right ascension").fill("120");
  await form.getByLabel("Declination").fill("-10");
  await form.getByLabel("Your name").fill("Ada");
  await page.getByRole("button", { name: "Plan the downloads" }).click();
  await expect(page).toHaveURL(/\/plans\/sha256/);
  await expect(page.getByTestId("plan-cost")).toContainText("15 MB");
  await page.goto("/start");
  await expect(page.getByTestId("field-form").getByLabel("Your name")).toHaveValue("Ada");
});

test("reviews a candidate: reopen, note and close, each with who and why", async ({ page }) => {
  await page.goto("/investigations/invset-000001/inv-000002");
  const decide = page.getByTestId("decide");
  await decide.getByLabel("Your name").fill("Ada");
  await decide.getByRole("button", { name: "Reopen" }).click();
  await expect(page.getByTestId("action-error")).toContainText("Say why");
  await decide.getByLabel("Why, or your note").fill("Gaia DR4 is out.");
  await decide.getByRole("button", { name: "Reopen" }).click();
  await expect(page.getByTestId("action-done")).toContainText("Reopened");
  await expect(page.getByTestId("transitions").locator("li")).toHaveCount(3);
  await expect(page.getByTestId("transitions")).toContainText("Gaia DR4 is out.");
  await decide.getByLabel("Why, or your note").fill("Bright neighbour within 3 arcsec.");
  await decide.getByRole("button", { name: "Add a note" }).click();
  await expect(page.getByTestId("notes")).toContainText("Bright neighbour");
  await decide.getByLabel("Why, or your note").fill("The neighbour explains the motion.");
  await decide.getByRole("button", { name: "Close it" }).click();
  await expect(page.getByTestId("action-done")).toContainText("Closed");
  await expect(decide.getByRole("button", { name: "Close it" })).toHaveCount(0);
  await expect(decide).toContainText("Closing is final");
});

test("promotion shows the command to copy", async ({ page }) => {
  await page.goto("/investigations/invset-000001/inv-000001");
  await page.getByText("Promote to a hypothesis test").click();
  await expect(page.getByTestId("copy-command")).toContainText("sdp investigate promote inv-000001");
});

test("approves a small plan and downloads it", async ({ page }) => {
  await page.goto(SMALL);
  const decision = page.getByTestId("plan-decision");
  await decision.getByLabel("Your name").fill("Ada");
  await decision.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("action-done")).toContainText("no signature was needed");
  await page.getByRole("button", { name: "Download now" }).click();
  await expect(page.getByTestId("execution")).toContainText("Fetched 3 of 3 items");
});

test("a large plan needs a signed approval, and can be refused with a reason", async ({ page }) => {
  await page.goto(LARGE);
  const decision = page.getByTestId("plan-decision");
  await expect(decision.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(decision.getByTestId("copy-command")).toContainText("sdp plan approve sha256:297aea48");
  await expect(page.getByTestId("plan-gaps")).toContainText("NIRSpec");
  await decision.getByLabel("Your name").fill("Ada");
  await decision.getByRole("button", { name: "Refuse" }).click();
  await expect(page.getByTestId("action-error")).toContainText("Say why");
  await decision.getByLabel("Why").fill("Spectra are missing anyway.");
  await decision.getByRole("button", { name: "Refuse" }).click();
  await expect(page.getByTestId("action-done")).toContainText("Refused");
  await expect(decision).toContainText("Spectra are missing anyway.");
});

test("one visitor's changes do not reach another", async ({ browser }) => {
  const a = await (await browser.newContext()).newPage();
  await a.goto("/experiments/exp-000004");
  await a.getByRole("button", { name: "Run it" }).click();
  await expect(a.getByTestId("action-done")).toBeVisible();
  const b = await (await browser.newContext()).newPage();
  await b.goto("/experiments/exp-000004");
  await expect(b.getByTestId("no-verdict")).toBeVisible();
  await expect(b.getByRole("button", { name: "Run it" })).toBeVisible();
});
