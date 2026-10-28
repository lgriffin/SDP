"""The ASGI app: ``/graphql`` for queries, ``/health``, and run/validate command endpoints (ADR-016).

Freezing and approval are not exposed here: they need a signer and belong to the CLI.
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
from sdp.kernel.ports.ledger import Ledger
from sdp.kernel.ports.storage import ArtifactStore


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

    return Starlette(
        routes=[
            Route("/health", health),
            Route("/experiments/{experiment_id}/run", command(run), methods=["POST"]),
            Route("/experiments/{experiment_id}/validate", command(validate), methods=["POST"]),
            Mount("/graphql", _GraphQL(runtime)),
        ]
    )
