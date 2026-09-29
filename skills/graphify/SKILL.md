---
name: graphify
description: Optional existing-code-graph navigation; use only when the CLI and a valid project graph are available.
---

# Optional code graph navigation
Use the repository's existing `graphify-out/graph.json` opportunistically for a focused relationship question. Run a scoped `graphify query`, `graphify path`, or `graphify explain` only when it helps the next action. Treat graph output as a navigation hint and confirm relevant symbols in current source; an existing graph may be stale.

If the CLI is absent, the graph is missing/malformed, or a query fails or is unhelpful, continue with `rg` / `git grep` and targeted file ranges. Do not block, install packages, request API keys, rebuild/update graphs, fetch URLs or dispatch semantic extraction agents as part of ordinary exploration. Graph creation and updates are separate explicitly requested work.

Graph locations, freshness and repository-specific commands remain project-local. The same CLI workflow serves Codex and Claude; no provider-specific implementation is needed.
