"""``sdp-api``: serve the app built from the environment, or ``--export-schema`` to print the SDL."""

from __future__ import annotations

import argparse
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

    from sdp.adapters.bootstrap import build_application
    from sdp.adapters.settings import Settings
    from sdp.api.app import create_app

    uvicorn.run(create_app(build_application(Settings.from_env())), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
