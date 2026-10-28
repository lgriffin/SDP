"""The child side of IsolatedVotableParser: VOTable on stdin, Arrow IPC stream on stdout.

python -I -m sdp.adapters.archives.isolated_parse --memory BYTES --cpu SECONDS < response.xml > table.arrows

The limits are set first, before the parser and pyarrow are imported, so they bound everything the
document can make the child do.
"""

from __future__ import annotations

import argparse
import sys


def _limit(memory_bytes: int, cpu_seconds: int) -> None:
    if not sys.platform.startswith("linux"):
        return
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isolated_parse")
    parser.add_argument("--memory", type=int, required=True)
    parser.add_argument("--cpu", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        _limit(args.memory, args.cpu)
        import pyarrow as pa

        from sdp.adapters.archives.untrusted import TAP_ERROR_EXIT
        from sdp.adapters.archives.votable import TapQueryError, parse
    except (MemoryError, ImportError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    body = sys.stdin.buffer.read()
    try:
        table = parse(body)
    except TapQueryError as exc:
        print(str(exc), file=sys.stderr)
        return TAP_ERROR_EXIT
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    sink = sys.stdout.buffer
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    sink.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
