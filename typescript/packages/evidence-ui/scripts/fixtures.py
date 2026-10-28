"""Regenerate src/lib/testing/fixtures.json: real sdp-api answers over the in-memory kernel.

    cd python && uv run python ../typescript/packages/evidence-ui/scripts/fixtures.py \
        ../typescript/packages/evidence-ui/src/lib/testing/fixtures.json

One approved VERIFIED experiment with a dossier, one REJECTED (data with another slope), one FAILED
(an injected task failure) and one only defined; then one investigation set over the synthetic field,
with one candidate parked, one closed and one promotion refused. Task durations differ between runs.
"""

import json
import re
import sys
from pathlib import Path

from sdp.api.app import create_app
from sdp.kernel.ports.driving import (
    ApproveVerdictCommand,
    AssembleDossierCommand,
    DefineExperimentCommand,
    FreezePredeclarationCommand,
)
from sdp.kernel.ports.investigate import InvestigateFieldCommand, TriageCommand
from sdp.testing.investigate import (
    gaia_archive,
    investigate_ports,
    observation_archive,
    synthetic_catalogue,
    synthetic_field,
    synthetic_profile,
    twomass_archive,
)
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series
from sdp.testing.wiring import build_in_memory_kernel
from starlette.testclient import TestClient

ts = (Path(__file__).resolve().parents[1] / "src/lib/adapters/graphql.ts").read_text()
summary = re.search(r'const SUMMARY = "([^"]+)"', ts).group(1)
set_summary = re.search(r'const SET_SUMMARY =\s*"([^"]+)"', ts).group(1)
queries = {
    m.group(1): m.group(2).replace("${SUMMARY}", summary).replace("${SET_SUMMARY}", set_summary)
    for m in re.finditer(r"(\w+): `([^`]+)`", ts)
}


catalogue = synthetic_catalogue()
k = build_in_memory_kernel(
    gateways={"synthetic-gaia": gaia_archive(catalogue), "synthetic-2mass": twomass_archive(catalogue)},
    investigate=investigate_ports(catalogue=catalogue, observations=observation_archive(catalogue)),
)
frozen, _ = k.kernel.freeze_predeclaration.execute(
    FreezePredeclarationCommand(SyntheticLinearProtocol().predeclare(), "leigh")
)


def define(series, **params):
    art = k.store.put(series, "application/json")
    return k.kernel.define_experiment.execute(
        DefineExperimentCommand("synthetic-linear", frozen.hash, {"series": art}, params, seed=42)
    )


verified = define(make_series())
k.kernel.run_experiment.execute(verified.experiment_id)
k.kernel.validate_experiment.execute(verified.experiment_id)
k.kernel.assemble_dossier.execute(
    AssembleDossierCommand(
        verified.experiment_id, ("Synthetic data with a planted slope; it says nothing about the sky.",)
    )
)
k.kernel.publish_bundle.approve(ApproveVerdictCommand(verified.experiment_id, "reviewer"))

rejected = define(make_series(slope=3.0, seed=11))
k.kernel.run_experiment.execute(rejected.experiment_id)
k.kernel.validate_experiment.execute(rejected.experiment_id)

failed = define(make_series(seed=13), inject_failure="residuals")
k.kernel.run_experiment.execute(failed.experiment_id)

pending = define(make_series(seed=17))

run = k.kernel.investigate_field.execute(InvestigateFieldCommand(synthetic_field(), synthetic_profile(), seed=11))
ranked = sorted(run.investigations, key=lambda i: -i.rank.score)
triage = k.kernel.triage_investigation
triage.execute(TriageCommand(ranked[1].investigation_id, "park", "leigh", "Revisit when Gaia DR4 lands."))
triage.execute(TriageCommand(ranked[-1].investigation_id, "close", "leigh", "Proper motion within the field spread."))
triage.execute(TriageCommand(ranked[0].investigation_id, "annotate", "leigh", "Check the plate positions."))
try:
    triage.execute(TriageCommand(ranked[0].investigation_id, "promote", "leigh", "", "sha256:" + "0" * 64))
except Exception as refused:  # recorded as InvestigationPromotionRefused
    print("promotion refused:", type(refused).__name__)

client = TestClient(create_app(k))


def q(name, **variables):
    r = client.post("/graphql", json={"query": queries[name], "variables": variables}).json()
    assert "errors" not in r, r
    return r["data"]


out = {
    "experiments": q("experiments")["experiments"],
    "experiment": {
        e: q("experiment", id=e)["experiment"]
        for e in (verified.experiment_id, rejected.experiment_id, failed.experiment_id, pending.experiment_id)
    },
    "ledger": q("ledger")["ledger"],
    "events": q("events", after=0, limit=1000)["events"],
    "investigationSets": q("investigationSets")["investigationSets"],
}
out["investigationSet"] = {s["id"]: q("investigationSet", id=s["id"])["investigationSet"] for s in out["investigationSets"]}
out["investigation"] = {
    i["id"]: q("investigation", id=i["id"])["investigation"]
    for s in out["investigationSet"].values()
    for i in s["investigations"]
}
out["lineage"] = {}
for e in out["experiment"].values():
    if e["manifestId"]:
        out["lineage"][e["manifestId"]] = q("lineage", entityId=e["manifestId"])["lineage"]
    out["lineage"]["experiment/" + e["id"]] = q("lineage", entityId="experiment/" + e["id"])["lineage"]
repo = str(Path(__file__).resolve().parents[4]) + "/"
Path(sys.argv[1]).write_text(json.dumps(out, indent=2, sort_keys=True).replace(repo, "") + "\n")
print(
    {k: len(v) for k, v in out.items()},
    [(e["state"], (e["verdict"] or {}).get("status")) for e in out["experiment"].values()],
)
print({k: len(v) for k, v in out["lineage"].items()})
