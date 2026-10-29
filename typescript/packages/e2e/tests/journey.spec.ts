// The key user journey, "Investigate one JWST field from start to finish", in one browser session on
// the demonstration server: select a field, see what MAST holds for it, approve a bounded download,
// read the calibrated image and its checks, see Gaia's stars on it, look into an ambiguous match and
// save the evidence and check it reproduces. The demonstration's JWST field is synthetic
// (sdp.testing.jwst); `sdp demo jwst` runs the same journey on the real M92 field.
import { expect, test } from "@playwright/test";

test("[INV-021] investigates one JWST field from start to finish", async ({ page }) => {
  // 1. Select a field: the JWST example fills in M92's outskirts and the image-check tests.
  await page.goto("/start");
  await page.getByTestId("examples").getByRole("link", { name: "JWST: the outskirts of M92" }).click();
  const form = page.getByTestId("field-form");
  await expect(form.getByLabel("Tests to run")).toHaveValue(/^jwst-image-check@/);
  await form.getByLabel("Your name").fill("Ada");

  // 2. Discover the JWST observations: the plan says what it will fetch from MAST and what it costs.
  await page.getByRole("button", { name: "Plan the downloads" }).click();
  await expect(page).toHaveURL(/\/plans\/sha256/);
  await expect(page.locator("main")).toContainText("synthetic-mast");
  await expect(page.getByTestId("plan-cost")).toContainText("8.42 MB");

  // 3. Approve a bounded download, then download it; the page follows the run until it finishes.
  const decision = page.getByTestId("plan-decision");
  await expect(decision.getByLabel("Your name")).toHaveValue("Ada");
  await decision.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("action-done")).toContainText("no signature was needed");
  await page.getByRole("button", { name: "Download now" }).click();
  await expect(page.getByTestId("execution")).toContainText("Fetched", { timeout: 15_000 });
  await page.getByRole("link", { name: "See the candidates" }).click();
  await expect(page).toHaveURL(/\/investigations\/invset-000101$/);

  // 4. The calibrated image the run read, with the checks its file went through.
  const card = page.getByTestId("set-images").getByRole("link");
  await expect(card).toContainText("12 Gaia stars: 1 stands out, 2 ambiguous.");
  await card.click();
  await expect(page.getByTestId("checks")).toBeVisible();

  // 5. Gaia's stars on the image, where their motion puts them on the image's date.
  const figure = page.getByTestId("field-image");
  await expect(figure.locator(".star")).toHaveCount(12);
  await expect(figure.locator('[data-status="missing"]')).toHaveCount(1);

  // 6. An ambiguous match, close up, with the hypotheses the test weighed.
  await page.getByTestId("notable").locator("tbody tr").filter({ hasText: "j-pair-a" }).getByRole("link", { name: "Zoom in" }).click();
  await expect(page.getByTestId("focus-matches")).toContainText("a blend of 2 Gaia stars");

  // 7. Save the evidence and check that the tests give the same results from it.
  await page.goto("/investigations/invset-000101");
  const keep = page.getByTestId("dossier");
  await keep.getByRole("button", { name: "Check it reproduces" }).click();
  await expect(keep.getByTestId("reproduced")).toContainText("the same 12 results", { timeout: 15_000 });
});
