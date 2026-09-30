# Canonical capabilities

ai-team is a router: it classifies a task, picks the cheapest capable Codex or Claude Code model and hands the original task to that native CLI. The runtime owns 8 global capabilities and six thin Codex/Claude entrypoints. One policy (`POLICY.md`), one routing configuration (`routing.json`), and one capability map (`capabilities.json`) are authoritative.

| Name | Runtime type | Scope | Providers | Load | Status | Reason |
|---|---|---|---|---|---|---|
| Router controls | script | global | shared | always | keep | Classification, context estimate, provider/tier choice, fallback, cooldowns, verification and cost tracking live in code; no prompt copies. |
| Native agent execution | adapter | global | Codex / Claude | lazy | keep | The original task goes to the native Codex / Claude Code CLI, which uses its own tools inside its own sandbox; thin interactive entrypoints load canonical roles. |
| Orchestration entrypoint | role | global | shared | lazy | keep | /team and Codex `team`: plan with ai-team, then run it. |
| Exploration | role | global | shared | lazy | keep | Interactive read-only investigation subagent. |
| Implementation | role | global | shared | lazy | keep | Interactive bounded-change subagent. |
| Architecture | role | global | shared | lazy | keep | Interactive read-only decision subagent. |
| Review | role | global | shared | lazy | keep | Interactive read-only diff review subagent. |
| Code graph support | optional skill | global | shared | conditional | optional | One line in the prompt (and guidance for interactive explorer/architect) only with an installed CLI and a valid local graph. |

`shared` means Codex and Claude. Routed runs send the original task plus a few router lines; role files serve only the interactive entrypoints. Deterministic controls add no prompt text, and ai-team carries no tool-, IDE- or service-specific solving logic: the agents investigate with their own tools.

Graphify is optional: one prompt line (and guidance for the interactive explorer/architect) only with an installed CLI and a valid local graph; otherwise the agent searches files itself. ai-team never installs, rebuilds or uploads graphs. Project graphs and facts stay in the project.

Provider adapters load canonical instructions through `ai-team instructions ROLE`. The six entrypoints are four Claude roles, Claude `/team`, and Codex `team`. No third-provider configuration is generated. Vendor catalogs, caches, historical inventories and backups are excluded from runtime and installation.

The historical audit discovered 4,661 definitions, not 4,661 runtime skills. The published inventory contains only aggregate audit evidence and canonical runtime membership. Existing role loading was already lazy, so no permanent-token reduction is claimed. Optional graph guidance is about 1 KiB rather than a full vendor pipeline.
