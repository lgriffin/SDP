"""T4: a plan that selects products and fits them to the profile's budget (INT-025), on the synthetic
JWST field with MAST-shaped records for two missions, several filters and single exposures."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from tests.usecase.test_jwst_slice import BUDGET, cutout_profile, run_slice, slice_kernel

from sdp.kernel.application.investigate import field_search_cone
from sdp.kernel.domain.integration import AcquisitionBudget, AcquisitionPlan, IntegrationRefused, PlanItem, select_items
from sdp.kernel.domain.investigation import (
    ObservationRecord,
    ProductRef,
    ProductSelection,
    ProfileBudget,
    TestProfile,
)
from sdp.kernel.ports.integration import PlanInvestigationCommand
from sdp.kernel.ports.investigate import CutoutCost, cutout_product_id
from sdp.testing.fakes.investigate import PlacedRecord
from sdp.testing.jwst import IMAGE_MJD, JWST_CENTRE, MAST, CuttingArchive, jwst_catalogue, jwst_field, jwst_i2d

pytestmark = [pytest.mark.tier("T4"), pytest.mark.req("INT-025")]

SIDE = 25.0
MB = 1_000_000
#: A deployment roomier than any profile here, so the profile's own budget is the one that binds.
ROOMY = AcquisitionBudget(5_000 * MB, 50, 3600.0, 0, 3_000 * MB)


def record(
    name: str, mission: str, instrument: str, band: str | None, *, single: bool = False, mjd: float = IMAGE_MJD
) -> PlacedRecord:
    pid = f"{name.removesuffix('.fits')}/{name}"
    return PlacedRecord(
        *JWST_CENTRE,
        ObservationRecord(
            product=ProductRef(MAST, pid, f"https://mast.invalid/{pid}"),
            mission=mission,
            instrument=instrument,
            product_type="image",
            epoch_mjd=mjd,
            size_bytes=5_000 * MB,
            licence="public",
            citation="synthetic",
            band=band,
            single_exposure=single,
        ),
    )


LISTED = (
    record("jw-o001_nircam_clear-f200w_i2d.fits", "JWST", "NIRCAM/IMAGE", "F200W"),
    record("jw-o001_nircam_clear-f444w_i2d.fits", "JWST", "NIRCAM/IMAGE", "F444W"),
    record("jw00001001_nrcb3_i2d.fits", "JWST", "NIRCAM/IMAGE", None, single=True),
    record("hst_1_05_wfc3_uvis_f657n_ick905_drc.fits", "HST", "WFC3/UVIS", "F657N", mjd=56902.0),
    record("hst_1_05_wfc3_uvis_f657n_ick905k5_drc.fits", "HST", "WFC3/UVIS", "F657N", single=True, mjd=56902.0),
    record("hst_2_01_wfc3_uvis_f657n_idx901_drc.fits", "HST", "WFC3/UVIS", "F657N", mjd=59318.0),
    record("hst_1_05_wfc3_uvis_f502n_ick905_drc.fits", "HST", "WFC3/UVIS", "F502N", mjd=56902.0),
    record("hst_1_07_acs_wfc_total_jck907_drc.fits", "HST", "ACS/WFC", None, mjd=56902.0),
)
#: What each cutout reads, as a header probe would find it; a string is the gateway's refusal.
COSTS: dict[str, Any] = {
    "jw-o001_nircam_clear-f200w_i2d": CutoutCost(300 * MB, 15 * MB),
    "jw-o001_nircam_clear-f444w_i2d": CutoutCost(80 * MB, 4 * MB),
    "hst_2_01_wfc3_uvis_f657n_idx901_drc": LookupError("(150.1, 2.2) is off the image"),
    "hst_1_05_wfc3_uvis_f657n_ick905_drc": CutoutCost(115 * MB, 6 * MB),
    "hst_1_05_wfc3_uvis_f502n_ick905_drc": CutoutCost(115 * MB, 6 * MB, 0.2),
}


@dataclass
class CostingArchive(CuttingArchive):
    """A cutting archive that also answers ``cutout_cost`` from a table, and counts the probes."""

    costs: dict[str, Any] = field(default_factory=dict)
    probed: list[str] = field(default_factory=list)
    max_records: int | None = None

    def cutout_cost(self, product: ProductRef, ra_deg: float, dec_deg: float, side_arcsec: float) -> CutoutCost:
        self.probed.append(product.product_id)
        cost = self.costs[product.product_id.partition("/")[0]]
        if isinstance(cost, Exception):
            raise cost
        assert isinstance(cost, CutoutCost)
        return cost


def archive(scale: float = 1.0) -> CostingArchive:
    """``scale`` shrinks every cost, so a plan can stay under the slice's automatic-approval threshold."""
    image = jwst_i2d(jwst_catalogue())
    products = {r.record.product.product_id: image for r in LISTED}
    costs = {
        k: replace(v, read_bytes=int(v.read_bytes * scale)) if isinstance(v, CutoutCost) else v
        for k, v in COSTS.items()
    }
    return CostingArchive(MAST, "mast:caom", records=LISTED, products=products, costs=costs)


def profile(max_bytes: int = 2_000 * MB, **select: Any) -> TestProfile:
    base: TestProfile = cutout_profile(SIDE)
    archives = tuple(
        replace(a, missions=("JWST", "HST"), match_radius_arcsec=15.0, select=ProductSelection(**select))
        for a in base.archives
    )
    return replace(base, profile_id="selection-slice", archives=archives, budget=ProfileBudget(max_bytes, 0.10))


def plan_with(gateway: CostingArchive, p: TestProfile) -> AcquisitionPlan:
    k = slice_kernel(gateway)
    assert k.kernel.plan_investigation is not None
    return k.kernel.plan_investigation.execute(PlanInvestigationCommand(jwst_field(), p, ROOMY, seed=1))


def cut(name: str) -> str:
    stem = name.removesuffix(".fits")
    return cutout_product_id(f"{stem}/{name}", *jwst_field_centre(), SIDE)


def jwst_field_centre() -> tuple[float, float]:
    ra, dec, _ = field_search_cone(jwst_field())
    return ra, dec


def test_a_plan_keeps_one_combined_mosaic_per_instrument_and_filter_costed_at_what_it_reads() -> None:
    gateway = archive()
    plan = plan_with(gateway, profile())
    images = {i.target: i.expected_bytes for i in plan.items if i.source_id == MAST}
    assert images == {
        cut("jw-o001_nircam_clear-f200w_i2d.fits"): 300 * MB,
        cut("jw-o001_nircam_clear-f444w_i2d.fits"): 80 * MB,
        cut("hst_1_05_wfc3_uvis_f657n_ick905_drc.fits"): 115 * MB,
    }, "a cutout costs the rows it reads, not the square it keeps"
    reasons = {e["target"].partition("/")[0]: e["reason"] for e in plan.constraints["excluded"]}
    assert reasons == {
        "jw00001001_nrcb3_i2d": "it names no band to group it by",
        "hst_1_05_wfc3_uvis_f657n_ick905k5_drc": "a single exposure; the profile keeps combined products only",
        "hst_2_01_wfc3_uvis_f657n_idx901_drc": "the cutout misses the image: (150.1, 2.2) is off the image",
        "hst_1_05_wfc3_uvis_f502n_ick905_drc": "the image holds 20% of the cutout, under the profile's 50%",
        "hst_1_07_acs_wfc_total_jck907_drc": "it names no band to group it by",
    }
    assert len(gateway.probed) == 5, "every product that could still be kept is probed once, and no other"
    assert gateway.fetches == [] and gateway.cuts == [], "planning reads headers, never pixels"
    selection = plan.constraints["selection"]
    assert selection["nominal_bytes"] == 1_800 * MB
    assert [p["target"] for p in selection["archives"][MAST]["probes"]] == gateway.probed
    assert plan.budget.max_bytes == 2_000 * MB, "the profile's cap is the plan's hard cap"


def test_jwst_comes_first_and_hubble_gives_way_when_the_budget_runs_short() -> None:
    plan = plan_with(archive(), profile(max_bytes=420 * MB))
    images = {i.target for i in plan.items if i.source_id == MAST}
    assert images == {cut("jw-o001_nircam_clear-f200w_i2d.fits")}, "300 MB of 378 nominal; F444W would pass it"
    reasons = {e["target"].partition("/")[0]: e["reason"] for e in plan.constraints["excluded"]}
    assert "over the nominal budget of 378000000" in reasons["jw-o001_nircam_clear-f444w_i2d"]
    assert "over the nominal budget" in reasons["hst_1_05_wfc3_uvis_f657n_ick905_drc"]
    assert plan.cost.total_bytes <= plan.budget.max_bytes


def test_the_harness_still_rederives_the_items_from_the_recorded_candidates() -> None:
    plan = plan_with(archive(), profile())
    candidates = [PlanItem.from_document(c) for c in plan.constraints["candidates"]]
    items, gaps = select_items(plan.requirements, plan.matches, candidates)
    assert items == plan.items and gaps == plan.gaps
    assert AcquisitionPlan.from_document(plan.to_document()) == plan


def test_execution_cuts_only_what_the_selection_kept_and_refuses_the_rest() -> None:
    gateway = archive(scale=0.05)
    plan, run = run_slice(slice_kernel(gateway), profile=profile())
    assert plan.budget.max_bytes == BUDGET.max_bytes, "the slice's deployment budget is tighter than the profile's"
    kept = {i.target for i in plan.items if i.source_id == MAST}
    assert set(gateway.cuts) == kept and len(gateway.cuts) == 3
    refused = {r["target"] for r in run["refused"][0]}
    assert {e["target"] for e in plan.constraints["excluded"]} <= {t.partition("@")[0] for t in refused}


def test_a_profile_without_a_selection_plans_as_before() -> None:
    plain = cutout_profile(SIDE)
    plan = plan_with(
        archive(), replace(plain, archives=tuple(replace(a, missions=("JWST", "HST")) for a in plain.archives))
    )
    assert "selection" not in plan.constraints and "excluded" not in plan.constraints
    assert sum(1 for i in plan.items if i.source_id == MAST) == len(LISTED)


def test_a_listing_cut_at_the_gateways_row_limit_is_refused_not_planned_as_complete() -> None:
    gateway = archive()
    gateway.max_records = len(LISTED)
    with pytest.raises(IntegrationRefused, match="its limit of 8"):
        plan_with(gateway, profile())
