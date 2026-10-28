# Vendored: Impeccable

This directory, and `.claude/agents/impeccable-*.md`, are a verbatim copy of the
Claude Code project install of [Impeccable](https://impeccable.style) by Paul Bakaus.

- Upstream: https://github.com/pbakaus/impeccable
- Commit: 7510e233b228864606888d252abacf5e7148bf87 (2026-10-28)
- Skill version: 4.8.0, engine 0.1.18 (`scripts/VERSION`)
- Licence: Apache-2.0, see [LICENSE](LICENSE); third-party notices in [NOTICE.md](NOTICE.md)

Only this file was added; nothing upstream was changed. The detector engine is a
platform binary the launcher (`scripts/impeccable`) downloads on first run from the
upstream GitHub releases and verifies against `scripts/engine-checksums.txt`. If the
download is blocked, the skill falls back to reading PRODUCT.md / DESIGN.md directly.

Upstream's optional Claude Code hooks (detector after each Edit/Write, deep pass on
Stop) are not enabled here. To turn them on, copy the `hooks` block from upstream's
`.claude/settings.json` into this repo's `.claude/settings.json`.

To update, copy upstream's `.claude/skills/impeccable/` and `.claude/agents/impeccable-*.md`
over these files (or run `npx impeccable install` from the repo root) and bump the commit above.
