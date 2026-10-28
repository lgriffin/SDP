"""The child side of IsolatedVotableParser: VOTable on stdin, Arrow IPC stream on stdout.

    python -I -m sdp.adapters.archives.isolated_parse < response.xml > table.arrows
"""

from __future__ import annotations

import sys

import pyarrow as pa

from sdp.adapters.archives.untrusted import TAP_ERROR_EXIT
from sdp.adapters.archives.votable import TapQueryError, parse


def main() -> int:
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
