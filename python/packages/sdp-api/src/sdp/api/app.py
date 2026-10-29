"""The ASGI app: ``/graphql`` for queries, ``/health``, run/validate command endpoints (ADR-016),
``POST /investigations``, which the field viewer and the evidence UI call to run InvestigateField
(INV-012), ``POST /investigations/{id}/triage`` for the unsigned triage steps, and the planner
(ADR-020): ``POST /plans``, ``/plans/{id}/approve``, ``/plans/{id}/refuse`` and ``/plans/{id}/execute``.

Signed steps are not exposed here: freezing, approval of a verdict or of a plan above its budget
threshold, and promotion need a signer or a frozen confirmatory predeclaration and belong to the CLI.
``/approve`` approves a plan below the threshold, or one whose signed ledger approval the CLI has
already written. Triage and refusals record their actor and reason like the CLI.
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
from sdp.kernel.application.planning import DEFAULT_BUDGET, plan_view
from sdp.kernel.domain.errors import DomainError
from sdp.kernel.domain.integration import AcquisitionBudget
from sdp.kernel.domain.investigation import Field, TestProfile, parse_region
from sdp.kernel.ports.integration import ApprovePlanCommand, ExecutePlanCommand, PlanInvestigationCommand
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
                field_from(body),
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

    def refused(exc: Exception) -> Response:
        if isinstance(exc, KeyError):
            return JSONResponse({"error": str(exc.args[0]) if exc.args else "not found"}, status_code=404)
        return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=409)

    async def plan(request: Request) -> Response:
        use_case = runtime.kernel.plan_investigation
        if use_case is None:
            return JSONResponse({"error": "the planner is not wired on this server"}, status_code=503)
        try:
            body = await request.json()
            budget = body.get("budget")
            command = PlanInvestigationCommand(
                field_from(body),
                TestProfile.from_document(body["profile"]),
                AcquisitionBudget.from_document(budget) if budget else getattr(runtime, "budget", DEFAULT_BUDGET),
                actor=str(body.get("actor", "viewer")),
                seed=int(body.get("seed", 0)),
            )
        except (KeyError, TypeError, ValueError, DomainError) as exc:
            return JSONResponse({"error": f"bad request: {exc}", "type": type(exc).__name__}, status_code=400)
        try:
            drafted = use_case.execute(command)
        except DomainError as exc:
            return refused(exc)
        return JSONResponse({"plan_id": drafted.plan_id, **view(drafted.plan_id)})

    def view(plan_id: str) -> dict[str, Any]:
        query = runtime.kernel.query_plans
        record = query.plan(plan_id) if query else None
        if record is None:
            raise KeyError(f"no plan {plan_id}")
        return plan_view(record)

    async def decide(request: Request) -> Response:
        use_case = runtime.kernel.approve_plan
        if use_case is None:
            return JSONResponse({"error": "the planner is not wired on this server"}, status_code=503)
        plan_id = request.path_params["plan_id"]
        refusing = request.url.path.endswith("/refuse")
        try:
            body = await request.json()
            actor = str(body["actor"])
            reason = str(body.get("reason", "")).strip() if refusing else None
        except (KeyError, TypeError, ValueError) as exc:
            return JSONResponse({"error": f"bad request: {exc}", "type": type(exc).__name__}, status_code=400)
        if refusing and not reason:
            return JSONResponse({"error": "a refusal records why: send a reason"}, status_code=400)
        try:
            use_case.execute(ApprovePlanCommand(plan_id, actor, refuse_reason=reason))
        except (KeyError, DomainError) as exc:
            if not refusing and "signed ledger approval is required" in str(exc):
                return JSONResponse(
                    {
                        "error": f"{exc}; sign it with: sdp plan approve {plan_id} --actor {actor}",
                        "type": type(exc).__name__,
                    },
                    status_code=409,
                )
            return refused(exc)
        return JSONResponse(view(plan_id))

    async def execute(request: Request) -> Response:
        use_case = runtime.kernel.execute_plan
        if use_case is None:
            return JSONResponse({"error": "the planner is not wired on this server"}, status_code=503)
        plan_id = request.path_params["plan_id"]
        try:
            body = await request.json() if await request.body() else {}
            actor = str(body.get("actor", "viewer"))
        except (TypeError, ValueError, AttributeError) as exc:
            return JSONResponse({"error": f"bad request: {exc}", "type": type(exc).__name__}, status_code=400)
        try:
            use_case.execute(ExecutePlanCommand(plan_id, actor))
        except (KeyError, DomainError) as exc:
            return refused(exc)
        return JSONResponse(view(plan_id))

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/plans", plan, methods=["POST"]),
            Route("/plans/{plan_id}/approve", decide, methods=["POST"]),
            Route("/plans/{plan_id}/refuse", decide, methods=["POST"]),
            Route("/plans/{plan_id}/execute", execute, methods=["POST"]),
            Route("/investigations", investigate, methods=["POST"]),
            Route("/investigations/{investigation_id}/triage", triage, methods=["POST"]),
            Route("/experiments/{experiment_id}/run", command(run), methods=["POST"]),
            Route("/experiments/{experiment_id}/validate", command(validate), methods=["POST"]),
            Mount("/graphql", _GraphQL(runtime)),
        ]
    )


def field_from(body: dict[str, Any]) -> Field:
    return Field(
        parse_region(str(body["region"])),
        epoch_jyear=float(body.get("epoch_jyear", 2016.0)),
        label=str(body.get("label", "")),
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
