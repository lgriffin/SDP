"""T1: which listed products a plan keeps under a profile's selection and budget (INT-026)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sdp.kernel.application.planning import DEFAULT_BUDGET, effective_budget, nominal_bytes
from sdp.kernel.domain.errors import InvariantViolation
from sdp.kernel.domain.integration import ProductOption, select_products
from sdp.kernel.domain.investigation import ArchiveRole, ArchiveSelection, ProductSelection, ProfileBudget
from sdp.testing.investigate import synthetic_profile

pytestmark = [pytest.mark.tier("T1"), pytest.mark.req("INT-026")]


def option(
    target: str,
    mission: str = "JWST",
    band: str | None = "F200W",
    *,
    priority: int = 0,
    single: bool = False,
    epoch: float | None = 59805.0,
) -> ProductOption:
    group = (("mission", mission), ("instrument", "CAM"), ("band", band))
    return ProductOption("mast", target, group, priority, single, epoch)


ASKED: list[str] = []
"""The products the last ``run`` asked the cost of, in order."""


def run(
    options: list[ProductOption], costs: dict[str, int | str], **kw: object
) -> tuple[dict[str, int], dict[str, str]]:
    ASKED.clear()
    asked = ASKED

    def cost(o: ProductOption) -> int | str:
        asked.append(o.target)
        return costs.get(o.target, 100)

    settings: dict[str, object] = {"combined_only": True, "max_products": None, "nominal_bytes": 10_000} | kw
    kept, excluded = select_products(options, cost, **settings)  # type: ignore[arg-type]
    return kept, {e.target: e.reason for e in excluded}


def test_one_product_per_group_and_the_others_say_which_one_serves_it() -> None:
    kept, excluded = run(
        [option("old", epoch=56902.0), option("new", epoch=59805.0), option("f444w", band="F444W")], {}
    )
    assert kept == {"new": 100, "f444w": 100}, "the newest product of a group is kept"
    assert excluded == {"old": "its group is served by new"}
    assert ASKED == ["new", "f444w"], "only a product that could still be kept is probed"


def test_combined_products_come_before_single_exposures_and_a_product_with_no_filter_is_left_out() -> None:
    kept, excluded = run(
        [option("exposure", single=True, epoch=60000.0), option("visit"), option("total", band=None)], {}
    )
    assert kept == {"visit": 100}
    assert excluded["exposure"].startswith("a single exposure")
    assert excluded["total"] == "it names no band to group it by"
    kept, _ = run([option("exposure", single=True)], {}, combined_only=False)
    assert kept == {"exposure": 100}, "single exposures may serve a group when the profile allows"


def test_the_budget_fills_by_mission_priority_and_a_product_that_does_not_fit_gives_way() -> None:
    options = [
        option("jwst-a", band="F090W"),
        option("jwst-b", band="F200W"),
        option("hst-big", "HST", "F657N", priority=1, epoch=59318.0),
        option("hst-small", "HST", "F657N", priority=1, epoch=56902.0),
    ]
    costs: dict[str, int | str] = {"jwst-a": 400, "jwst-b": 400, "hst-big": 300, "hst-small": 150}
    kept, excluded = run(options, costs, nominal_bytes=1000, spent_bytes=50)
    assert kept == {"jwst-a": 400, "jwst-b": 400, "hst-small": 150}
    assert excluded["hst-big"] == "its 300 bytes would bring the plan to 1150, over the nominal budget of 1000"
    assert sum(kept.values()) + 50 <= 1000


def test_a_probe_refusal_is_the_reason_and_the_next_product_of_the_group_is_tried() -> None:
    kept, excluded = run(
        [option("off", epoch=60000.0), option("on")], {"off": "the cutout misses the image: (1, 2) is off the image"}
    )
    assert kept == {"on": 100} and excluded["off"].startswith("the cutout misses the image")


def test_max_products_caps_the_kept_set() -> None:
    kept, excluded = run([option(f"p{i}", band=f"F{i}00W") for i in range(1, 5)], {}, max_products=2)
    assert len(kept) == 2 and sum(r == "the profile keeps at most 2 products" for r in excluded.values()) == 2


def test_selection_is_deterministic_whatever_order_the_archive_lists_products_in() -> None:
    options = [option(f"p{i}", band=f"F{i % 3}00W", epoch=59000.0 + i % 2) for i in range(9)]
    first = run(options, {})
    assert run(list(reversed(options)), {}) == first


def test_a_profile_budget_only_tightens_the_deployment_budget_and_holds_back_its_reserve() -> None:
    profile = replace(synthetic_profile(), budget=ProfileBudget(2_000_000_000, 0.10))
    budget = effective_budget(DEFAULT_BUDGET, profile)
    assert (
        budget.max_bytes == 2_000_000_000 and budget.approval_threshold_bytes == DEFAULT_BUDGET.approval_threshold_bytes
    )
    assert nominal_bytes(budget, profile) == 1_800_000_000
    tight = replace(DEFAULT_BUDGET, max_bytes=1_000_000_000, approval_threshold_bytes=100)
    assert effective_budget(tight, profile) == tight, "a profile never loosens what the deployment allows"
    assert nominal_bytes(tight, profile) == 900_000_000, "the reserve applies to the cap that binds"
    small = ProfileBudget(100, 0.5)
    assert effective_budget(DEFAULT_BUDGET, replace(profile, budget=small)).approval_threshold_bytes == 100
    plain = synthetic_profile()
    assert effective_budget(DEFAULT_BUDGET, plain) is DEFAULT_BUDGET and nominal_bytes(DEFAULT_BUDGET, plain) == 5e9


def test_budgets_and_selections_are_validated_and_round_trip() -> None:
    for bad in ({"max_bytes": 0}, {"max_bytes": 2.5}, {"max_bytes": True}, {"max_bytes": 10, "reserve_fraction": 1.0}):
        with pytest.raises(InvariantViolation):
            ProfileBudget(**bad)
    for bad_selection in ({"one_per": ("filter",)}, {"one_per": ()}, {"max_products": 0}, {"min_coverage": 1.5}):
        with pytest.raises(InvariantViolation):
            ProductSelection(**bad_selection)
    with pytest.raises(InvariantViolation, match="lists no products"):
        ArchiveSelection("2mass", ArchiveRole.CATALOGUE, "x", columns=("a",), select=ProductSelection())
    selection = ProductSelection(("mission", "band"), max_products=3, min_coverage=0.25)
    assert ProductSelection.from_document(selection.to_document()) == selection
    profile = replace(synthetic_profile(), budget=ProfileBudget(2_000_000_000))
    assert type(profile).from_document(profile.to_document()) == profile


def test_a_profile_without_a_budget_or_selection_keeps_its_hash() -> None:
    profile = synthetic_profile()
    assert "budget" not in profile.to_document()
    assert all("select" not in a for a in profile.to_document()["archives"])
