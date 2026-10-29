"""Regenerate src/lib/testing/jwst.json: real sdp-api answers for the synthetic JWST field.

    cd python && uv run python ../typescript/packages/evidence-ui/scripts/jwst-fixtures.py \
        ../typescript/packages/evidence-ui/src/lib/testing/jwst.json

The JWST vertical slice (sdp.testing.jwst) planned, approved and executed through the planner: one
NIRCam-like image with twelve Gaia stars, one missing from the image and a blended pair. Its ids are
renumbered from 101 so they never meet those in fixtures.json, which a separate in-memory kernel made.
The picture of the image is kept as base64 under "previews", keyed by its artifact id.
"""

import base64
import json
import re
import sys
from pathlib import Path

from starlette.testclient import TestClient

from sdp.adapters.association import BayesFactorAssociation, LikelihoodRatioAssociation
from sdp.adapters.imaging.photutils_detector import PhotutilsDetector
from sdp.api.app import create_app
from sdp.kernel.application.planning import DEFAULT_BUDGET
from sdp.kernel.ports.integration import ApprovePlanCommand, ExecutePlanCommand, PlanInvestigationCommand
from sdp.testing.integration import gaia_descriptor, mast_descriptor
from sdp.testing.investigate import gaia_archive, investigate_ports, planning_discovery
from sdp.testing.jwst import GAIA, MAST, jwst_archive, jwst_catalogue, jwst_field, jwst_profile, jwst_resolver
from sdp.testing.wiring import build_in_memory_kernel
from sdp_investigate.imaging import ImageCounterpart

ts = (Path(__file__).resolve().parents[1] / "src/lib/adapters/graphql.ts").read_text()
set_summary = re.search(r'const SET_SUMMARY =\s*"([^"]+)"', ts).group(1)
plan_fields = re.search(r'const PLAN = "([^"]+)"', ts).group(1)
queries = {
    m.group(1): m.group(2).replace("${SET_SUMMARY}", set_summary).replace("${PLAN}", plan_fields)
    for m in re.finditer(r"(\w+): `([^`]+)`", ts)
}

catalogue = jwst_catalogue()
ports = investigate_ports(ImageCounterpart(), catalogue=catalogue, resolver=jwst_resolver(catalogue))
ports.observation_gateways = {MAST: jwst_archive(catalogue)}
ports.services = {"detector": PhotutilsDetector(), "associators": (BayesFactorAssociation(), LikelihoodRatioAssociation())}
k = build_in_memory_kernel(
    gateways={GAIA: gaia_archive(catalogue)},
    investigate=ports,
    discovery=planning_discovery(mast_descriptor(), gaia_descriptor()),
)
plan = k.kernel.plan_investigation.execute(
    PlanInvestigationCommand(jwst_field(), jwst_profile(), DEFAULT_BUDGET, actor="leigh", seed=1)
)
k.kernel.approve_plan.execute(ApprovePlanCommand(plan.plan_id, "leigh"))
k.kernel.execute_plan.execute(ExecutePlanCommand(plan.plan_id, "leigh"))

client = TestClient(create_app(k))


def q(name, **variables):
    r = client.post("/graphql", json={"query": queries[name], "variables": variables}).json()
    assert "errors" not in r, r
    return r["data"]


out = {"investigationSets": q("investigationSets")["investigationSets"], "plans": q("plans")["plans"]}
out["investigationSet"] = {s["id"]: q("investigationSet", id=s["id"])["investigationSet"] for s in out["investigationSets"]}
out["investigation"] = {
    i["id"]: q("investigation", id=i["id"])["investigation"]
    for s in out["investigationSet"].values()
    for i in s["investigations"]
}
out["previews"] = {
    image["artifact_id"]: base64.b64encode(client.get(f"/artifacts/{image['artifact_id']}/preview.png").content).decode()
    for s in out["investigationSet"].values()
    for image in s["images"]
}
text = json.dumps(out, indent=2, sort_keys=True)
text = re.sub(r"\b(invset|inv|bundle|dossier)-0000(\d\d)\b", r"\1-0001\2", text)
repo = str(Path(__file__).resolve().parents[4]) + "/"
Path(sys.argv[1]).write_text(text.replace(repo, "") + "\n")
print({k: len(v) for k, v in out.items()})
