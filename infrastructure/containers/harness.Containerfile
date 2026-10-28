# sdp-harness image: spec-harness (ears, trace, conform, rederive, ratchet, report) from the pinned
# pnpm-lock.yaml. Runs as the image's unprivileged node user (NFR-009).
# Build from the repository root: podman build -f infrastructure/containers/harness.Containerfile -t sdp-harness .

FROM docker.io/library/node:22-bookworm-slim
ENV PNPM_HOME=/opt/pnpm COREPACK_HOME=/opt/corepack CI=true
WORKDIR /opt/sdp
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY typescript/ typescript/
COPY contracts/ contracts/
RUN corepack enable \
 && pnpm install --frozen-lockfile --prod --filter @sdp/spec-harness... \
 && chown -R node:node /opt/sdp /opt/corepack
USER node
WORKDIR /work
ENTRYPOINT ["node", "/opt/sdp/typescript/packages/spec-harness/src/cli.ts"]
CMD ["--help"]
