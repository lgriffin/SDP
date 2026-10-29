"""``sdp-api``: serve the app built from the environment, or ``--export-schema`` to print the SDL."""

from __future__ import annotations

import argparse
import os
import sys

from sdp.api.schema import export_schema


def main() -> None:
    parser = argparse.ArgumentParser(prog="sdp-api")
    parser.add_argument("--export-schema", action="store_true", help="print schema.graphql and exit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.export_schema:
        sys.stdout.write(export_schema())
        return

    import uvicorn

    from sdp.adapters.archives.registration import RegisteredArchives
    from sdp.adapters.bootstrap import archive_transport, build_application
    from sdp.adapters.postgres.capabilities import PostgresCapabilityRegistry
    from sdp.adapters.settings import Settings, secret
    from sdp.api.app import create_app

    settings = Settings.from_env()
    token = secret(os.environ, "SDP_API_TOKEN")
    archives = RegisteredArchives(
        PostgresCapabilityRegistry(settings.database_url), lambda name: archive_transport(settings, name, 1.0)
    )
    app = create_app(build_application(settings), token, archives=archives)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
