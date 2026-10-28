# Clean-room reproduction image (T9, NFR-004): both toolchains installed from the pinned lock files
# of the checkout being released, nothing else. infrastructure/reproduce.sh builds it and runs
# `sdp reproduce` and the harness inside it. Runs as an unprivileged user (NFR-009).

FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM docker.io/library/python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=docker.io/library/node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=docker.io/library/node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
 && groupadd --gid 10001 sdp \
 && useradd --uid 10001 --gid sdp --home-dir /home/sdp --create-home --shell /bin/bash sdp
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy COREPACK_HOME=/opt/corepack CI=true
WORKDIR /opt/sdp
COPY --chown=10001:10001 . .
RUN corepack enable && mkdir -p /opt/corepack && chown 10001:10001 /opt/corepack
USER 10001:10001
RUN cd python && uv sync --frozen --all-packages \
 && cd .. && corepack pnpm install --frozen-lockfile --filter @sdp/spec-harness...
ENTRYPOINT ["infrastructure/reproduce.sh", "--inside"]
