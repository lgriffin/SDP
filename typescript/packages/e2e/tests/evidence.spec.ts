import { expect, test } from "@playwright/test";

test.describe("results", () => {
  test("lists every experiment with where it is and its verdict", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Results" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Results" })).toBeVisible();
    const rows = page.getByTestId("experiments").locator("tbody tr");
    await expect(rows).toHaveCount(4);
    await expect(rows.filter({ hasText: "exp-000001" })).toContainText("Verified");
    await expect(rows.filter({ hasText: "exp-000002" })).toContainText("Rejected");
    await expect(rows.filter({ hasText: "exp-000003" })).toContainText("Run failed");
  });

  test("filters by lifecycle state", async ({ page }) => {
    await page.goto("/experiments");
    await page.getByLabel("State").selectOption("failed");
    await expect(page).toHaveURL(/state=failed/);
    await expect(page.getByTestId("experiments").locator("tbody tr")).toHaveCount(1);
    await page.goto("/experiments?state=published");
    await expect(page.getByTestId("empty")).toHaveText("No experiments in state published.");
  });
});

test.describe("dossier page", () => {
  test("a verified experiment shows its gates, dossier, tasks and provenance", async ({ page }) => {
    await page.goto("/experiments");
    await page.getByRole("link", { name: "exp-000001" }).click();
    await expect(page.getByTestId("verdict")).toContainText("Verified");
    await expect(page.getByTestId("verdict")).toContainText("Every check set before the run passed");
    await expect(page.getByTestId("gates").locator("li")).toHaveCount(2);
    await expect(page.getByTestId("gate-ruler")).toHaveCount(2);
    await expect(page.getByTestId("dossier")).toContainText("says nothing about the sky");
    await page.getByText("Steps of the run").click();
    await expect(page.getByTestId("tasks").locator("tbody tr")).toHaveCount(2);
    await page.getByText("Where the result came from").click();
    await expect(page.getByTestId("lineage").locator("li")).toHaveCount(2);
    await expect(page.getByTestId("next-step")).toContainText("sdp publish");
  });

  test("a rejection highlights the blocking gate that decided it", async ({ page }) => {
    await page.goto("/experiments/exp-000002");
    await expect(page.getByTestId("verdict")).toContainText("Rejected");
    const decisive = page.locator("li.decisive");
    await expect(decisive).toHaveCount(1);
    await expect(decisive).toHaveAttribute("data-gate", "SYN-G-01");
    await expect(decisive).toContainText("Failed");
    await expect(decisive).toContainText("This check decided the verdict.");
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
    await page.getByText("Where the result came from").click();
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
    await expect(page.getByTestId("chain-ok")).toContainText("Chain intact");
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
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Candidates" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Investigations" })).toBeVisible();
    const rows = page.getByTestId("sets").locator("tbody tr");
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText("field-");
    await page.getByRole("link", { name: "invset-000001" }).click();
    const candidates = page.getByTestId("candidates").locator("tbody tr");
    await expect(candidates).toHaveCount(5);
    await expect(candidates.filter({ hasText: "gaia:DR3:1" })).toContainText("Parked");
    await expect(candidates.filter({ hasText: "gaia:DR3:4" })).toContainText("Closed");
    await expect(page.getByTestId("chance")).toContainText("16.33%");
    await page.getByRole("link", { name: /To review/ }).click();
    await expect(candidates).toHaveCount(3);
    await page.getByText("Stages of the search").click();
    await expect(page.getByTestId("stages").locator("tbody tr")).toHaveCount(4);
    await expect(page.getByTestId("stages")).not.toContainText("failed");
    await page.getByText("Where the candidates came from").click();
    await expect(page.getByTestId("examined")).toHaveText("60");
    await page.getByText("The numbers").click();
    await expect(page.getByTestId("nulls").locator("tbody tr")).toHaveCount(1);
  });

  test("an investigation shows its results beside the null rate, its evidence and its triage", async ({ page }) => {
    await page.goto("/investigations/invset-000001/inv-000002");
    await expect(page.getByRole("heading", { level: 1, name: "gaia:DR3:1" })).toBeVisible();
    await expect(page.getByTestId("exploratory")).toContainText("cannot confirm anything");
    await page.getByText("The numbers").click();
    await expect(page.getByTestId("results").locator("tbody tr")).toHaveCount(1);
    await expect(page.getByTestId("results")).toContainText("pm-excess@1.0.0");
    await expect(page.getByTestId("known-as")).toHaveText("known as HD 999999");
    await expect(page.getByTestId("observations").first()).toContainText("reaching back to 1884");
    await expect(page.getByTestId("transitions").locator("li")).toHaveCount(2);
    await expect(page.getByTestId("transitions")).toContainText("Revisit when Gaia DR4 lands.");
    await expect(page.getByTestId("evidence-used")).toContainText("gaia:DR3");
  });

  test("[INV-022] a field's evidence can be checked to reproduce from its saved dossier", async ({ page }) => {
    await page.goto("/investigations/invset-000101");
    const keep = page.getByTestId("dossier");
    await expect(keep.getByRole("heading", { name: "Save the evidence" })).toBeVisible();
    await expect(keep.getByTestId("save-dossier")).toHaveCount(0);
    await expect(keep).toContainText("has no dossier to save");
    await expect(keep).toContainText("sdp investigate reproduce invset-000101.zip");
    await keep.getByRole("button", { name: "Check it reproduces" }).click();
    await expect(keep.getByTestId("reproducing")).toContainText("Re-running the tests from the saved file");
    await expect(keep.getByRole("button", { name: "Checking…" })).toBeDisabled();
    // The page reads the check again every few seconds until it has finished.
    await expect(keep.getByTestId("reproduced")).toContainText("the tests gave the same 12 results (seed 1)", { timeout: 15_000 });
    await expect(keep.getByRole("button", { name: "Check again" })).toBeEnabled();
    expect((await page.request.get("/investigations/invset-000101/dossier.zip")).status()).toBe(404);
  });

  test("[INV-020] a JWST field shows its image with Gaia stars, and zooms in on an ambiguous match", async ({ page }) => {
    await page.goto("/investigations/invset-000101");
    const card = page.getByTestId("set-images").getByRole("link");
    await expect(card).toContainText("12 Gaia stars: 1 stands out, 2 ambiguous.");
    await card.click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText("Images of synthetic JWST field");
    const figure = page.getByTestId("field-image");
    await expect(figure.locator("image")).toHaveAttribute("href", /\/artifacts\/sha256%3A[0-9a-f]+\/preview\.png/);
    await expect(figure.locator(".star")).toHaveCount(12);
    await expect(figure.locator('[data-status="missing"]')).toHaveCount(1);
    await expect(figure.locator('[data-status="ambiguous"]')).toHaveCount(2);
    await expect(page.getByTestId("legend")).toContainText("Where expected 9");
    await expect(page.getByTestId("checks")).toContainText("not checked against an envelope");
    const picture = await page.request.get(await figure.locator("image").getAttribute("href") as string);
    expect(picture.headers()["content-type"]).toBe("image/png");

    const notable = page.getByTestId("notable").locator("tbody tr");
    await expect(notable).toHaveCount(3);
    await expect(notable.first()).toContainText("j-vanished");
    await notable.filter({ hasText: "j-pair-a" }).getByRole("link", { name: "Zoom in" }).click();
    await expect(page.getByTestId("zoomed")).toContainText("j-pair-a: ambiguous match");
    await expect(page.getByTestId("focus-matches")).toContainText("a blend of 2 Gaia stars");
    await expect(page.getByTestId("focus-matches")).toContainText("Also kept: j-pair-b");

    await page.goto("/investigations/invset-000101/images");
    await expect(page.getByTestId("field-image").locator('a[data-status="missing"]')).toHaveAttribute("href", "/investigations/invset-000101/inv-000101");
    await page.getByTestId("notable").getByRole("link", { name: "Candidate" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "j-vanished" })).toBeVisible();
    await expect(page.getByTestId("sightings")).toContainText("Not on the image");
    await expect(page.getByTestId("sightings")).toContainText("No detection on this image is a plausible match");
    await page.getByLabel("Show Gaia stars").uncheck();
    await expect(page.getByTestId("sightings").locator(".star")).toHaveCount(0);
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
    "/start",
    "/experiments",
    "/experiments/exp-000001",
    "/experiments/exp-000002",
    "/experiments/exp-000003",
    "/ledger",
    "/events",
    "/investigations",
    "/investigations/invset-000001",
    "/investigations/invset-000001/inv-000002",
    "/investigations/invset-000101",
    "/investigations/invset-000101/images",
    "/investigations/invset-000101/images?star=j-pair-a",
    "/investigations/invset-000101/inv-000101",
    "/plans",
    `/plans/${encodeURIComponent("sha256:297aea48cac6cf41832f6b5207e6cd04ed5034bb611d1c5d72b2a4232ffc1181")}`,
    "/help",
  ]) {
    await page.goto(path);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});
