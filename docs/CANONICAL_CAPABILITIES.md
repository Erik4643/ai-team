# Canonical capabilities

The runtime uses 15 global reusable capabilities and six thin Codex/Claude entrypoints. One policy (`POLICY.md`), one routing configuration (`routing.json`), and one capability map (`capabilities.json`) are authoritative.

| Name | Runtime type | Scope | Providers | Load | Status | Reason |
|---|---|---|---|---|---|---|
| Deterministic orchestration controls | script | global | shared | always | replace | Triage, evidence routing (diagnostics/ adapters), budgeting, verification, mapping, routing and cost estimation stay in existing code; no prompt copies. |
| Orchestration | role | global | shared | lazy | keep | One lead workflow; current policy and routing remain authoritative. |
| Targeted exploration | role | global | shared | lazy | keep | Read only evidence needed for the next action; no ecosystem discovery. |
| Implementation | role | global | shared | lazy | keep | Bounded changes from established evidence. |
| Debugging / test repair | role | global | shared | conditional | merge | One root-cause workflow, including test failure diagnosis; selected existing roles only. |
| Architecture | role | global | shared | lazy | keep | Existing architect decides only unresolved cross-module choices. |
| Review / disagreement resolution | role | global | shared | lazy | merge | One reviewer plus conditional judge; retain existing risk gates. |
| Research | role | global | shared | lazy | keep | External facts with exact versions and official sources when needed. |
| Dependency investigation | role | global | shared | conditional | merge | Relevant manifest/lockfile/caller evidence; no separate agent or package installation. |
| Migration planning | role | global | shared | conditional | merge | Phases, compatibility, checks and recovery augment existing roles. |
| Security review | role | global | shared | conditional | merge | Evidence-based trust-boundary review; no always-on external scanner. |
| Performance investigation | role | global | shared | conditional | merge | Measure and profile before changing a hot path; use existing roles. |
| Structured handoff | script | global | shared | conditional | keep | Existing make_handoff and role output schemas; protocol is a reference, not repeated prompt content. |
| Code graph support | optional skill | global | shared | conditional | optional | One compact global workflow; valid local graph and installed CLI required, otherwise targeted exploration. |
| Provider adapters | adapter | global | Codex / Claude | lazy | merge | Generate format-only pointers to canonical instructions; never copy vendor caches. |

`shared` means Codex and Claude. Detailed role text loads only when selected; at most two relevant workflow supplements load. Deterministic controls add no prompt text. Optional guidance is discarded first when budgets are exceeded.

Graphify is optional: use a valid existing local graph when its CLI is available and helpful; otherwise use targeted file searches. This integration never installs, rebuilds or uploads graphs. Project graphs and facts stay in the project.

Provider adapters load canonical instructions through `ai-team instructions ROLE`. The six entrypoints are four Claude roles, Claude `/team`, and Codex `team`. No third-provider configuration is generated. Vendor catalogs, caches, historical inventories and backups are excluded from runtime and installation.

The historical audit discovered 4,661 definitions, not 4,661 runtime skills. The published inventory contains only aggregate audit evidence and canonical runtime membership. Existing role loading was already lazy, so no permanent-token reduction is claimed. Optional graph guidance is about 1 KiB rather than a full vendor pipeline.
