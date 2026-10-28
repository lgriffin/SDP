import { expect, test } from "@playwright/test";

test.describe("experiments", () => {
  test("lists every experiment with state and verdict", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "Experiments" })).toBeVisible();
    const rows = page.getByTestId("experiments").locator("tbody tr");
    await expect(rows).toHaveCount(4);
    await expect(rows.filter({ hasText: "exp-000001" })).toContainText("VERIFIED");
    await expect(rows.filter({ hasText: "exp-000002" })).toContainText("REJECTED");
    await expect(rows.filter({ hasText: "exp-000003" })).toContainText("failed");
  });

  test("filters by lifecycle state", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("State").selectOption("failed");
    await expect(page).toHaveURL(/state=failed/);
    await expect(page.getByTestId("experiments").locator("tbody tr")).toHaveCount(1);
    await page.goto("/?state=published");
    await expect(page.getByTestId("empty")).toHaveText("No experiments in state published.");
  });
});

test.describe("dossier page", () => {
  test("a verified experiment shows its gates, dossier, tasks and provenance", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "exp-000001" }).click();
    await expect(page.getByTestId("verdict")).toContainText("VERIFIED");
    await expect(page.getByTestId("gates").locator("tbody tr")).toHaveCount(2);
    await expect(page.getByTestId("dossier")).toContainText("says nothing about the sky");
    await expect(page.getByTestId("tasks").locator("tbody tr")).toHaveCount(2);
    await expect(page.getByTestId("lineage").locator("li")).toHaveCount(2);
  });

  test("a rejection highlights the blocking gate that decided it", async ({ page }) => {
    await page.goto("/experiments/exp-000002");
    await expect(page.getByTestId("verdict")).toContainText("REJECTED");
    const decisive = page.locator("tr.decisive");
    await expect(decisive).toHaveCount(1);
    await expect(decisive).toHaveAttribute("data-gate", "SYN-G-01");
    await expect(decisive).toContainText("fail");
  });

  test("a failed run shows the failing task and its traceback", async ({ page }) => {
    await page.goto("/experiments/exp-000003");
    const failure = page.getByTestId("failure");
    await expect(failure).toContainText("residuals");
    await expect(failure).toContainText("InjectedFailure");
    await failure.getByText("Traceback", { exact: true }).click();
    await expect(failure.locator("pre")).toContainText("run_plan");
  });

  test("an experiment that has not run says so", async ({ page }) => {
    await page.goto("/experiments/exp-000004");
    await expect(page.getByTestId("no-verdict")).toHaveText("No verdict yet: the experiment is defined.");
  });

  test("the manifest downloads as JSON", async ({ page, request }) => {
    await page.goto("/experiments/exp-000001");
    const href = await page.getByTestId("manifest-link").getAttribute("href");
    const response = await request.get(href ?? "");
    expect(response.ok()).toBe(true);
    const manifest = (await response.json()) as { verdict: { status: string }; schema_version: string };
    expect(manifest.verdict.status).toBe("VERIFIED");
    expect(manifest.schema_version).toMatch(/^1\./);
  });

  test("an unknown experiment is a 404", async ({ page }) => {
    const response = await page.goto("/experiments/exp-missing");
    expect(response?.status()).toBe(404);
    await expect(page.getByTestId("error")).toHaveText("No experiment exp-missing");
  });
});

test.describe("ledger and events", () => {
  test("the ledger shows an intact chain of signed entries", async ({ page }) => {
    await page.goto("/ledger");
    await expect(page.getByTestId("chain-ok")).toContainText("2 entries");
    const rows = page.getByTestId("ledger").locator("tbody tr");
    await expect(rows.nth(0)).toContainText("predeclaration");
    await expect(rows.nth(1)).toContainText("approval");
    await rows.nth(1).getByRole("link", { name: "exp-000001" }).click();
    await expect(page).toHaveURL(/\/experiments\/exp-000001$/);
  });

  test("events link back to their experiments", async ({ page }) => {
    await page.goto("/events");
    const rows = page.getByTestId("events").locator("tbody tr");
    await expect(rows.first()).toContainText("PredeclarationFrozen");
    await expect(page.getByTestId("events")).toContainText("VerdictIssued");
    await page.goto("/events?after=1000");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("No events after #1000");
  });
});

test.describe("investigations", () => {
  test("lists the set with its stages, and opens it", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Investigations" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Investigations" })).toBeVisible();
    const rows = page.getByTestId("sets").locator("tbody tr");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("field-");
    await rows.first().getByRole("link", { name: "invset-000001" }).click();
    await expect(page.getByTestId("stages").locator("tbody tr")).toHaveCount(4);
    await expect(page.getByTestId("stages")).not.toContainText("failed");
    await expect(page.getByTestId("examined")).toHaveText("60");
    const candidates = page.getByTestId("candidates").locator("tbody tr");
    await expect(candidates).toHaveCount(5);
    await expect(candidates.filter({ hasText: "gaia:DR3:1" })).toContainText("parked");
    await expect(candidates.filter({ hasText: "gaia:DR3:4" })).toContainText("closed");
    await expect(page.getByTestId("nulls").locator("tbody tr")).toHaveCount(1);
  });

  test("an investigation shows its results beside the null rate, its evidence and its triage", async ({ page }) => {
    await page.goto("/investigations/invset-000001/inv-000002");
    await expect(page.getByRole("heading", { level: 1, name: "gaia:DR3:1" })).toBeVisible();
    await expect(page.getByTestId("exploratory")).toContainText("cannot confirm anything");
    await expect(page.getByTestId("results").locator("tbody tr")).toHaveCount(1);
    await expect(page.getByTestId("results")).toContainText("pm-excess@1.0.0");
    await expect(page.getByTestId("known-as")).toHaveText("known as HD 999999");
    await expect(page.getByTestId("observations").first()).toContainText("reaching back to 1884");
    await expect(page.getByTestId("transitions").locator("li")).toHaveCount(2);
    await expect(page.getByTestId("transitions")).toContainText("Revisit when Gaia DR4 lands.");
    await expect(page.getByTestId("evidence-used")).toContainText("gaia:DR3");
  });

  test("notes are shown, and an investigation from another set is a 404", async ({ page }) => {
    await page.goto("/investigations/invset-000001/inv-000001");
    await expect(page.getByTestId("notes")).toContainText("Check the plate positions.");
    const response = await page.goto("/investigations/invset-other/inv-000001");
    expect(response?.status()).toBe(404);
    expect((await page.goto("/investigations/invset-missing"))?.status()).toBe(404);
  });
});

test("pages never scroll sideways", async ({ page }) => {
  for (const path of [
    "/",
    "/experiments/exp-000001",
    "/experiments/exp-000003",
    "/ledger",
    "/events",
    "/investigations",
    "/investigations/invset-000001",
    "/investigations/invset-000001/inv-000002",
  ]) {
    await page.goto(path);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});
