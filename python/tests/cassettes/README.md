# Archive cassettes (ADR-015, DSI-007)

Recorded archive responses, one directory per source (`gaia`, `2mass`, `ps1`, `cds-xmatch`), one file per request, named by the SHA-256 of its method, URL and body. With `SDP_ARCHIVE_MODE=replay`, adapters answer only from here and never call an archive.

This directory starts empty: the cloud sandbox that wrote the adapters cannot reach the archives, and responses are never written by hand. The **Archive live smoke** workflow (`.github/workflows/archive-smoke.yml`, weekly and on demand) records the schema query and a small Pleiades cone for every source and uploads them as the `cassettes` artifact. A run started by hand also pushes them to an `archive-cassettes/run-<id>` branch. To adopt a recording, merge that branch's files here through a pull request and review them like code: a changed column list there is the drift the job reports.

To record locally with network access:

```
cd python
uv run sdp archive smoke --baseline tests/cassettes --record tests/cassettes
```
