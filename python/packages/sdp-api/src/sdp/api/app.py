"""The ASGI app: ``/graphql`` for queries, ``/health``, run/validate command endpoints (ADR-016),
``POST /investigations``, which the field viewer and the evidence UI call to run InvestigateField
(INV-012), ``POST /investigations/{id}/triage`` for the unsigned triage steps, and the planner
(ADR-020): ``POST /plans``, ``/plans/{id}/approve``, ``/plans/{id}/refuse`` and ``/plans/{id}/execute``
(``{"background": true}`` runs it as a job, INV-018), and ``/archives`` with ``POST /archives/register``,
which reads what each archive offers so a new installation can plan (INV-019).

Signed steps are not exposed here: freezing, approval of a verdict or of a plan above its budget
threshold, and promotion need a signer or a frozen confirmatory predeclaration and belong to the CLI.
``/approve`` approves a plan below the threshold, or one whose signed ledger approval the CLI has
already written. Triage and refusals record their actor and reason like the CLI.
"""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable
from typing import Any, Protocol

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from strawberry.asgi import GraphQL

from sdp.adapters.products.preview import preview_png
from sdp.api.jobs import REGISTER_JOB, REPRODUCE_JOBS, RUNNING, JobBusy, Jobs, plan_job, reproduce_job
from sdp.api.schema import schema
from sdp.kernel.application.kernel import Kernel
from sdp.kernel.application.planning import DEFAULT_BUDGET, plan_view
from sdp.kernel.application.set_bundle import DOSSIER_MEDIA_TYPE
from sdp.kernel.domain.errors import DomainError, IntegrityError
from sdp.kernel.domain.ids import ArtifactId
from sdp.kernel.domain.integration import AcquisitionBudget
from sdp.kernel.domain.investigation import Field, TestProfile, parse_region
from sdp.kernel.ports.integration import ApprovePlanCommand, ExecutePlanCommand, PlanInvestigationCommand
from sdp.kernel.ports.investigate import InvestigateFieldCommand, InvestigationRun, TriageCommand
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.storage import ArtifactStore

BUSY = "another field run is going on this server; it reads archives for minutes, so start this when it finishes"
#: Artifacts are content-addressed, so an answer for one never changes.
PICTURES_KEPT = 32
IMMUTABLE = {"cache-control": "private, max-age=31536000, immutable"}
UNSIGNED_TRIAGE = frozenset({"park", "reopen", "close", "annotate"})
"""Triage the UI may send. Promotion is left to the CLI (INV-009)."""


class Runtime(Protocol):
    @property
    def kernel(self) -> Kernel: ...

    @property
    def store(self) -> ArtifactStore: ...

    @property
    def ledger(self) -> Ledger: ...


class Archives(Protocol):
    """The archives plans are matched against: what is registered, and registering them again."""

    def registered(self) -> list[dict[str, Any]]: ...

    def register(self) -> list[dict[str, Any]]: ...


class _GraphQL(GraphQL[dict[str, Any], None]):
    def __init__(self, runtime: Runtime, jobs: Jobs, archives: Archives | None) -> None:
        super().__init__(schema, graphql_ide="graphiql")
        self.runtime = runtime
        self.jobs = jobs
        self.archives = archives

    async def get_context(self, request: Any, response: Any = None) -> dict[str, Any]:
        return {"kernel": self.runtime.kernel, "request": request, "jobs": self.jobs, "archives": self.archives}


class RequireToken:
    """With a token, every route but ``/health`` needs ``Authorization: Bearer <token>``.

    The desktop app sets one (SDP_API_TOKEN), because its API listens on the person's own machine
    where any page in their browser could otherwise send it a cross-site POST or rebind a name to
    127.0.0.1. A browser cannot add this header cross-site without a CORS preflight, which is refused."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] != "/health":
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                await JSONResponse({"error": "missing or wrong API token"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(
    runtime: Runtime, token: str | None = None, *, archives: Archives | None = None, jobs: Jobs | None = None
) -> Starlette:
    work = jobs if jobs is not None else Jobs()
    # One InvestigateField at a time: its use case holds the running plan's gate on the instance, so a
    # second run alongside a background download could replace or clear that gate mid-run (INV-018).
    runs = threading.Lock()

    def busy() -> Response:
        return JSONResponse(
            {"error": BUSY, "type": "RunInProgress"},
            status_code=409,
        )

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
        if not runs.acquire(blocking=False):
            return busy()
        try:
            run = use_case.execute(command)
        except DomainError as exc:
            return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=409)
        finally:
            runs.release()
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

    def unwired() -> Response:
        return JSONResponse(
            {"error": "the planner is not wired on this server", "type": "PlannerUnavailable"}, status_code=503
        )

    def json_object(body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise TypeError("the request body is a JSON object")
        return body

    def actor_of(body: dict[str, Any], default: str | None = None) -> str:
        actor = body.get("actor", default)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is a non-blank string")
        return actor.strip()

    def budget_of(body: dict[str, Any]) -> AcquisitionBudget:
        """The server's budget, or a request's own when it is no looser on any limit: a caller may
        tighten the operator's SDP_PLAN_* limits, never relax them (INT-014, DSI-010)."""
        server: AcquisitionBudget = getattr(runtime, "budget", DEFAULT_BUDGET)
        if "budget" not in body:
            return server
        asked = AcquisitionBudget.from_document(json_object(body["budget"]))
        looser = [
            name
            for name in ("max_bytes", "max_requests_per_source", "max_wall_seconds", "approval_threshold_bytes")
            if getattr(asked, name) > getattr(server, name)
        ]
        if asked.min_nas_headroom_bytes < server.min_nas_headroom_bytes:
            looser.append("min_nas_headroom_bytes")
        if looser:
            raise ValueError(f"the budget may only tighten the server's limits; looser: {', '.join(looser)}")
        return asked

    def refused(exc: Exception) -> Response:
        if isinstance(exc, KeyError):
            return JSONResponse({"error": str(exc.args[0]) if exc.args else "not found"}, status_code=404)
        return JSONResponse({"error": str(exc), "type": type(exc).__name__}, status_code=409)

    async def plan(request: Request) -> Response:
        use_case = runtime.kernel.plan_investigation
        if use_case is None:
            return unwired()
        try:
            body = json_object(await request.json())
            command = PlanInvestigationCommand(
                field_from(body),
                TestProfile.from_document(body["profile"]),
                budget_of(body),
                actor=actor_of(body, "viewer"),
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
            return unwired()
        plan_id = request.path_params["plan_id"]
        refusing = request.url.path.endswith("/refuse")
        try:
            body = json_object(await request.json())
            actor = actor_of(body)
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

    def job_view(plan_id: str) -> dict[str, Any]:
        job = work.get(plan_job(plan_id))
        return {**view(plan_id), "run": job.to_document() if job else None}

    async def execute(request: Request) -> Response:
        use_case = runtime.kernel.execute_plan
        if use_case is None:
            return unwired()
        plan_id = request.path_params["plan_id"]
        try:
            body = json_object(await request.json()) if await request.body() else {}
            actor = actor_of(body, "viewer")
            background = body.get("background", False)
            if not isinstance(background, bool):
                raise TypeError("background is true or false")
        except (TypeError, ValueError, AttributeError) as exc:
            return JSONResponse({"error": f"bad request: {exc}", "type": type(exc).__name__}, status_code=400)
        command = ExecutePlanCommand(plan_id, actor)
        current = work.get(plan_job(plan_id))
        if current is not None and current.state == RUNNING:
            return JSONResponse(job_view(plan_id))  # already downloading: nothing new starts
        if not background:
            if not runs.acquire(blocking=False):
                return busy()
            try:
                use_case.execute(command)
            except (KeyError, DomainError) as exc:
                return refused(exc)
            finally:
                runs.release()
            return JSONResponse(job_view(plan_id))
        try:
            state = view(plan_id)["state"]
        except KeyError as exc:
            return refused(exc)
        if state not in ("approved", "auto_approved"):
            return JSONResponse({"error": f"plan {plan_id} is {state}; only an approved plan runs"}, status_code=409)
        if not runs.acquire(blocking=False):
            return busy()

        def run_plan() -> None:
            try:
                use_case.execute(command)
            finally:
                runs.release()

        # The run reads archives for minutes, so it answers now and the UI reads the job's state (INV-018).
        _, started = work.start(plan_job(plan_id), run_plan)
        if not started:
            runs.release()
        return JSONResponse(job_view(plan_id), status_code=202 if started else 200)

    pictures: dict[str, bytes] = {}

    def artifact_of(request: Request) -> ArtifactId | Response:
        try:
            artifact = ArtifactId(request.path_params["artifact_id"])
        except (DomainError, ValueError) as exc:
            return JSONResponse({"error": f"bad artifact id: {exc}"}, status_code=400)
        if not runtime.store.exists(artifact):
            return JSONResponse({"error": f"no artifact {artifact.value}"}, status_code=404)
        return artifact

    def artifact(request: Request) -> Response:
        """An artifact's bytes, as stored: a FITS cutout, an envelope, a stage document (INV-020)."""
        found = artifact_of(request)
        if isinstance(found, Response):
            return found
        return Response(runtime.store.get(found), media_type=runtime.store.media_type(found), headers=IMMUTABLE)

    def preview(request: Request) -> Response:
        """A FITS image drawn for the eye, pixel y upward; the overlay comes from the set's ``images`` (INV-020)."""
        found = artifact_of(request)
        if isinstance(found, Response):
            return found
        picture = pictures.get(found.value)
        if picture is None:
            try:
                picture = preview_png(runtime.store.get(found))
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=415)
            # An artifact never changes, so its picture is drawn once; the oldest is forgotten first.
            pictures[found.value] = picture
            while len(pictures) > PICTURES_KEPT:
                pictures.pop(next(iter(pictures)))
        return Response(picture, media_type="image/png", headers=IMMUTABLE)

    def set_dossier(request: Request) -> Response:
        """The set's evidence dossier as one RO-Crate, for the app's Save button (INV-021)."""
        use_case = runtime.kernel.set_dossier
        if use_case is None:
            return JSONResponse({"error": "Investigate is not wired on this server"}, status_code=503)
        set_id = request.path_params["set_id"]
        try:
            bundle = use_case.build(set_id)
        except KeyError:
            return JSONResponse({"error": f"no investigation set {set_id}"}, status_code=404)
        except IntegrityError as exc:  # evidence the set records is missing: no partial dossier
            return JSONResponse({"error": str(exc)}, status_code=409)
        disposition = {"content-disposition": f'attachment; filename="{set_id}-dossier.zip"'}
        return Response(bundle, media_type=DOSSIER_MEDIA_TYPE, headers=disposition)

    def reproduce_set(request: Request) -> Response:
        """Build the set's dossier and re-run its Test stage from it alone (INV-022). With images that
        takes minutes, so it runs as a job and the UI reads the report with GET."""
        use_case, query = runtime.kernel.set_dossier, runtime.kernel.query_investigations
        if use_case is None or query is None:
            return JSONResponse({"error": "Investigate is not wired on this server"}, status_code=503)
        set_id = request.path_params["set_id"]
        if query.set(set_id) is None:
            return JSONResponse({"error": f"no investigation set {set_id}"}, status_code=404)
        if request.method == "GET":
            job = work.get(reproduce_job(set_id))
            return JSONResponse({"reproduction": job.to_document() if job else None})

        def reproduce() -> dict[str, Any]:
            return use_case.reproduce(use_case.build(set_id)).to_document()

        try:
            # Each reproduction re-runs a whole Test stage: one at a time keeps the API answering.
            job, started = work.start(reproduce_job(set_id), reproduce, alone_among=REPRODUCE_JOBS)
        except JobBusy as busy:
            other = busy.running.removeprefix(REPRODUCE_JOBS)
            message = f"set {other} is being reproduced; try again when it has finished"
            return JSONResponse({"error": message}, status_code=409)
        return JSONResponse({"reproduction": job.to_document()}, status_code=202 if started else 200)

    def archive_sources(_: Request) -> Response:
        if archives is None:
            return JSONResponse({"error": "archive registration is not wired on this server"}, status_code=503)
        job = work.get(REGISTER_JOB)
        return JSONResponse({"sources": archives.registered(), "registration": job.to_document() if job else None})

    def register_archives(request: Request) -> Response:
        if archives is None:
            return JSONResponse({"error": "archive registration is not wired on this server"}, status_code=503)
        # Reading MAST's descriptor from the VO takes minutes, so it runs as a job too (INV-019).
        job, started = work.start(REGISTER_JOB, archives.register)
        return JSONResponse(
            {"sources": archives.registered(), "registration": job.to_document()}, status_code=202 if started else 200
        )

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/plans", plan, methods=["POST"]),
            Route("/plans/{plan_id}/approve", decide, methods=["POST"]),
            Route("/plans/{plan_id}/refuse", decide, methods=["POST"]),
            Route("/plans/{plan_id}/execute", execute, methods=["POST"]),
            Route("/archives", archive_sources),
            Route("/artifacts/{artifact_id}", artifact),
            Route("/artifacts/{artifact_id}/preview.png", preview),
            Route("/archives/register", register_archives, methods=["POST"]),
            Route("/investigations", investigate, methods=["POST"]),
            Route("/investigations/{investigation_id}/triage", triage, methods=["POST"]),
            Route("/investigations/{set_id}/dossier.zip", set_dossier),
            Route("/investigations/{set_id}/reproduce", reproduce_set, methods=["GET", "POST"]),
            Route("/experiments/{experiment_id}/run", command(run), methods=["POST"]),
            Route("/experiments/{experiment_id}/validate", command(validate), methods=["POST"]),
            Mount("/graphql", _GraphQL(runtime, work, archives)),
        ],
        middleware=[Middleware(RequireToken, token=token)] if token else [],
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
