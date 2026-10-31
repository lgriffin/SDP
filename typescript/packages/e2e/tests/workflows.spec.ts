// The scientist's workflows end to end on the demonstration server. Each test has its own browser
// context, so its own copy of the synthetic data: buttons here never change another test's page.
import { expect, test } from "@playwright/test";

const LARGE = `/plans/${encodeURIComponent("sha256:297aea48cac6cf41832f6b5207e6cd04ed5034bb611d1c5d72b2a4232ffc1181")}`;
const SMALL = `/plans/${encodeURIComponent("sha256:54c1021fcaf5706dfd1d4023b6f8cca9315f961ca6bd1fe88537ece815b70a88")}`;

test("home lists what is waiting and where each piece of work is", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("demonstration")).toContainText("synthetic");
  const waiting = page.getByTestId("waiting").locator("li");
  await expect(waiting).toHaveCount(6);
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

test("[INV-023] compares a candidate with catalogues side by side, and pulls more", async ({ page }) => {
  await page.goto("/investigations/invset-000001/inv-000001");
  await page.getByTestId("compare-link").click();
  await expect(page.getByRole("heading", { name: "Compare with catalogues" })).toBeVisible();
  const pull = page.getByTestId("pull");
  // nothing asked yet, so every catalogue is ticked
  await expect(pull.getByRole("checkbox")).toHaveCount(6);
  await expect(pull.getByRole("checkbox", { checked: true })).toHaveCount(6);
  await pull.getByLabel("Your name").fill("Ada");
  await pull.getByRole("button", { name: "Pull references" }).click();
  await expect(page.getByTestId("pull-done")).toContainText("Asked 6 catalogues");
  const table = page.getByTestId("comparison");
  // a column for each catalogue with something in the cone; the others are named under the table
  await expect(table.locator("thead th")).toHaveCount(6);
  await expect(table.locator('th[data-source="gaia"]')).toContainText("the candidate itself");
  await expect(page.getByTestId("empty-pulls").locator('[data-source="simbad"]')).toContainText("nothing within 5″");
  await expect(page.getByTestId("findings")).toContainText("SIMBAD has nothing within 5″.");
  await expect(page.getByTestId("findings")).toContainText("Gaia DR3 has 2 sources within 5″");
  // every catalogue answered, so the form now offers them all again; a narrower cone drops the neighbour
  await pull.getByLabel("Search radius").selectOption("2");
  await pull.getByRole("checkbox", { name: /SIMBAD/ }).uncheck();
  await pull.getByRole("checkbox", { name: /2MASS/ }).uncheck();
  await pull.getByRole("checkbox", { name: /AllWISE/ }).uncheck();
  await pull.getByRole("checkbox", { name: /Pan-STARRS/ }).uncheck();
  await pull.getByRole("checkbox", { name: /VSX/ }).uncheck();
  await pull.getByRole("button", { name: "Pull references" }).click();
  await expect(page.getByTestId("pull-done")).toContainText("Asked 1 catalogue.");
  await expect(table.locator('th[data-source="gaia"]')).toContainText("1 within 2″");
  await page.getByText("7 pulls").click();
  await expect(page.getByTestId("pulls").locator("tbody tr")).toHaveCount(7);
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
  // [INV-018] The download runs on its own; the page follows it until it finishes.
  await expect(page.getByTestId("run-progress")).toContainText("Downloading and checking");
  await expect(page.getByTestId("run-progress").getByTestId("journey")).toContainText("Download and check the data");
  await expect(page.getByTestId("execution")).toContainText("Fetched 3 of 3 items", { timeout: 15_000 });
});

test("[INV-019] the JWST example fills in the field and its profile", async ({ page }) => {
  await page.goto("/start");
  await page.getByTestId("examples").getByRole("link", { name: "JWST: the outskirts of M92" }).click();
  await expect(page).toHaveURL(/example=jwst-m92/);
  const form = page.getByTestId("field-form");
  await expect(form.getByLabel("Right ascension")).toHaveValue("259.323");
  await expect(form.getByLabel("Declination")).toHaveValue("43.1505");
  await expect(form.getByLabel("Radius")).toHaveValue("0.004");
  await expect(form.getByText("This is 14 arcsec")).toBeVisible();
  await expect(form.getByLabel("Tests to run")).toHaveValue(/^jwst-image-check@/);
  await expect(page.getByTestId("example-why")).toContainText("programme 1334");
  await expect(page.getByTestId("archives-setup")).toHaveCount(0);
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
