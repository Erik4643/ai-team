# Project context
(Canonical; AGENTS.md → here. Commands/layout: .ai/repo-map.json. Decisions: .ai/decisions.md. Keep it short.)

## Project facts
- What the project is and its main stack, in one or two lines.
- Conventions an agent cannot infer from the code (naming, where copy/config lives, generated dirs).

## Rules
- Inspect existing code and `git status` before editing; code wins over stale docs.
- Smallest correct change, in scope, following existing patterns. No unrelated refactors.
- No new dependencies, public API, schema, env or auth changes unless the task allows it.
- Never read or print secrets / `.env*` values. Never commit, push or delete branches unless asked.
- Never claim a check passed that was not run.
