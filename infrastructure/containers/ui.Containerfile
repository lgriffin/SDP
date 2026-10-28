# sdp-ui image: the evidence UI (SvelteKit, adapter-node) reading sdp-api's GraphQL at SDP_API_URL.
# Runs as the image's unprivileged node user (NFR-009).
# Build from the repository root: podman build -f infrastructure/containers/ui.Containerfile -t sdp-ui .

FROM docker.io/library/node:22-bookworm-slim AS build
ENV COREPACK_HOME=/opt/corepack CI=true
WORKDIR /opt/sdp
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY typescript/ typescript/
COPY contracts/ contracts/
RUN corepack enable \
 && pnpm install --frozen-lockfile --filter @sdp/evidence-ui... \
 && pnpm --filter @sdp/evidence-ui build

FROM docker.io/library/node:22-bookworm-slim
WORKDIR /opt/sdp-ui
COPY --from=build /opt/sdp/typescript/packages/evidence-ui/build ./build
COPY --from=build /opt/sdp/typescript/packages/evidence-ui/package.json ./package.json
ENV NODE_ENV=production HOST=0.0.0.0 PORT=3000
USER node
EXPOSE 3000
CMD ["node", "build/index.js"]
