"""Regenerate src/lib/testing/fixtures.json: real sdp-api answers over the in-memory kernel.

    cd python && uv run python ../typescript/packages/evidence-ui/scripts/fixtures.py \
        ../typescript/packages/evidence-ui/src/lib/testing/fixtures.json

One approved VERIFIED experiment with a dossier, one REJECTED (data with another slope), one FAILED
(an injected task failure) and one only defined. Task durations differ between runs.
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
from sdp.testing.synthetic import SyntheticLinearProtocol, make_series
from sdp.testing.wiring import build_in_memory_kernel
from starlette.testclient import TestClient

ts = (Path(__file__).resolve().parents[1] / "src/lib/adapters/graphql.ts").read_text()
summary = re.search(r'const SUMMARY = "([^"]+)"', ts).group(1)
queries = dict((m.group(1), m.group(2).replace("${SUMMARY}", summary)) for m in re.finditer(r"(\w+): `([^`]+)`", ts))


k = build_in_memory_kernel()
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
