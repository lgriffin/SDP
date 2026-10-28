"""The ASGI app: ``/graphql`` for queries, ``/health``, run/validate command endpoints (ADR-016),
``POST /investigations``, which the field viewer and the evidence UI call to run InvestigateField
(INV-012), and ``POST /investigations/{id}/triage`` for the unsigned triage steps.

Signed steps are not exposed here: freezing, approval and promotion need a signer or a frozen
confirmatory predeclaration and belong to the CLI. Triage records its actor and reason like the CLI.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from strawberry.asgi import GraphQL

from sdp.api.schema import schema
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.domain.investigation import Field, TestProfile, parse_region
from sdp.kernel.ports.investigate import InvestigateFieldCommand, InvestigationRun, TriageCommand
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.storage import ArtifactStore

UNSIGNED_TRIAGE = frozenset({"park", "reopen", "close", "annotate"})
"""Triage the UI may send. Promotion is left to the CLI (INV-009)."""


class Runtime(Protocol):
    @property
    def kernel(self) -> Kernel: ...

    @property
    def store(self) -> ArtifactStore: ...

    @property
    def ledger(self) -> Ledger: ...


class _GraphQL(GraphQL[dict[str, Any], None]):
    def __init__(self, runtime: Runtime) -> None:
        super().__init__(schema, graphql_ide="graphiql")
        self.runtime = runtime

    async def get_context(self, request: Any, response: Any = None) -> dict[str, Any]:
        return {"kernel": self.runtime.kernel, "request": request}


def create_app(runtime: Runtime) -> Starlette:
    def health(_: Request) -> Response:
        checks: dict[str, Any] = {}
        ok = True
        try:
            runtime.kernel.query_evidence.events(0, 1)
            checks["database"] = "ok"
        except Exception as exc:  # health reports any failure rather than raising
            ok, checks["database"] = False, f"error: {type(exc).__name__}"
        free = runtime.store.free_bytes()
        checks["store_free_bytes"] = free
        if free is not None and free < runtime.kernel.ports.min_free_bytes:
            ok = False
        problems = runtime.ledger.verify()
        checks["ledger"] = "ok" if not problems else f"{len(problems)} problem(s)"
        ok = ok and not problems
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    def command(action: Callable[[str], Any]) -> Callable[[Request], Response]:
        def endpoint(request: Request) -> Response:
            experiment_id = request.path_params["experiment_id"]
            try:
                result = action(experiment_id)
            except KeyError:
                return JSONResponse({"error": f"no experiment {experiment_id}"}, status_code=404)
            except DomainError as exc:
                return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=409)
            return JSONResponse(result)

        return endpoint

    def run(experiment_id: str) -> dict[str, Any]:
        e = runtime.kernel.run_experiment.execute(experiment_id)
        return {"experiment_id": e.experiment_id, "state": e.state.value}

    def validate(experiment_id: str) -> dict[str, Any]:
        report = runtime.kernel.validate_experiment.execute(experiment_id)
        return {"experiment_id": experiment_id, "verdict": report.verdict.status.value}

    async def investigate(request: Request) -> Response:
        use_case = runtime.kernel.investigate_field
        if use_case is None:
            return JSONResponse({"error": "Investigate is not wired on this server"}, status_code=503)
        try:
            body = await request.json()
            command = InvestigateFieldCommand(
                Field(
                    parse_region(str(body["region"])),
                    epoch_jyear=float(body.get("epoch_jyear", 2016.0)),
                    label=str(body.get("label", "")),
                ),
                TestProfile.from_document(body["profile"]),
                seed=int(body.get("seed", 0)),
                actor=str(body.get("actor", "viewer")),
                set_id=body.get("resume"),
            )
        except (KeyError, TypeError, ValueError, DomainError) as exc:
            return JSONResponse({"error": f"bad request: {exc}", "type": type(exc).__name__}, status_code=400)
        try:
            run = use_case.execute(command)
        except DomainError as exc:
            return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=409)
        return JSONResponse(run_summary(run))

    async def triage(request: Request) -> Response:
        use_case = runtime.kernel.triage_investigation
        if use_case is None:
            return JSONResponse({"error": "triage is not wired on this server"}, status_code=503)
        investigation_id = request.path_params["investigation_id"]
        try:
            body = await request.json()
            action = str(body["action"])
            command = TriageCommand(investigation_id, action, str(body["actor"]), str(body.get("reason", "")))
        except (KeyError, TypeError, ValueError) as exc:
            return JSONResponse({"error": f"bad request: {exc}", "type": type(exc).__name__}, status_code=400)
        if action not in UNSIGNED_TRIAGE:
            message = "promotion needs a frozen confirmatory predeclaration; use sdp investigate promote"
            return JSONResponse(
                {"error": message if action == "promote" else f"unknown triage action {action!r}"}, status_code=400
            )
        try:
            investigation = use_case.execute(command)
        except DomainError as exc:
            status = 404 if str(exc).startswith("no investigation ") else 409
            return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=status)
        return JSONResponse(
            {
                "investigation_id": investigation.investigation_id,
                "state": investigation.state.value,
                "transitions": len(investigation.transitions),
            }
        )

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/investigations", investigate, methods=["POST"]),
            Route("/investigations/{investigation_id}/triage", triage, methods=["POST"]),
            Route("/experiments/{experiment_id}/run", command(run), methods=["POST"]),
            Route("/experiments/{experiment_id}/validate", command(validate), methods=["POST"]),
            Mount("/graphql", _GraphQL(runtime)),
        ]
    )


def run_summary(run: InvestigationRun) -> dict[str, Any]:
    """What the viewer renders: the set, its stages, and each candidate with the sky positions of its
    objects (one for an object, one per member for a structure)."""
    s = run.investigation_set
    positions = {b.object_key: [b.object.ra_deg, b.object.dec_deg] for b in run.bundles}
    candidates = []
    for i in sorted(run.investigations, key=lambda i: -i.rank.score):
        keys = [i.candidate.object_key] if i.candidate.object_key is not None else list(i.candidate.members)
        candidates.append(
            {
                "investigation_id": i.investigation_id,
                "candidate": i.candidate.key,
                "score": i.rank.score,
                "state": i.state.value,
                "label": i.label,
                "positions": [positions[k] for k in keys if k in positions],
            }
        )
    return {
        "set_id": s.set_id,
        "field_id": s.field.field_id,
        "profile": f"{s.profile.profile_id}@{s.profile.version}",
        "profile_hash": s.profile_hash,
        "label": s.label,
        "stages": {stage.value: record.state.value for stage, record in s.stages.items()},
        "objects_examined": len(s.objects_examined),
        "candidates": candidates,
    }
