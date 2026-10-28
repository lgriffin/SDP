# sdp-kernel image: the kernel, adapters, science modules, CLI and API from the pinned uv.lock.
# Used by sdp-api.container and sdp-runner@.container. Runs as an unprivileged user (NFR-009).
# Build from the repository root: podman build -f infrastructure/containers/kernel.Containerfile -t sdp-kernel .

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM docker.io/library/python:3.12-slim-bookworm AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /opt/sdp/python
COPY python/ ./
RUN uv sync --frozen --no-dev --all-packages --no-editable

FROM docker.io/library/python:3.12-slim-bookworm
# git and ssh-keygen sign and commit ledger entries (ADR-004).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 sdp \
 && useradd --uid 10001 --gid sdp --home-dir /home/sdp --create-home --shell /usr/sbin/nologin sdp \
 && mkdir -p /data/artifacts /data/ledger /data/metrics \
 && chown -R 10001:10001 /data
COPY --from=build /opt/sdp/python/.venv /opt/sdp/python/.venv
COPY contracts/ /opt/sdp/contracts/
COPY experiments/ /opt/sdp/experiments/
ENV PATH=/opt/sdp/python/.venv/bin:$PATH SDP_REPO_ROOT=/opt/sdp PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/sdp
USER 10001:10001
ENTRYPOINT ["sdp"]
CMD ["--help"]
