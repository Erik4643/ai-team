# ai-team policy (`ai-team` applies it deterministically from `routing.json`; read only when orchestrating)

ai-team is a universal model router for any task: coding, research, computer problems, IDEs, tools, configuration and general investigation. It classifies the task, picks ONE native agent (Codex or Claude Code)
at the cheapest capable tier, passes the original task on, and handles failure. The agent solves the task with its own tools.

## 1. Classify (T0, 0 tokens)
Intent, complexity, context size and needs (edits, web research) come from the task text and cheap repo facts.
META / project facts / INSPECTION (git status, diff) / TOOL (run lint, tests, build) / review of a clean tree → local, 0 model calls.
QUESTION → read-only (repo cues → explorer with tools; general → native answer agent). RESEARCH → researcher with web tools.
REVIEW → reviewer of the requested subject; explicitly repository-related reviews use the current diff. PLAN / ARCHITECTURE → architect, read-only (`--apply` implements the decision).
"Find why X happens" → explorer (read-only diagnosis). Other action tasks → implementer (a native execution role, not a coding restriction).
Repository tasks use code evidence for auth/payment/migration risk; unrelated tasks do not depend on repository evidence.

## 2. One agent, cheapest capable tier
Edits start at T1 (MICRO/SMALL) or T2 (MEDIUM+); the economy budget starts one tier lower. Read-only roles start at T1
(architect on COMPLEX+ at T2). Edits whose estimated context exceeds `long_context_tokens` start at T2 at least.
Plan-time tiers never include T3. There is no explorer → architect → implementer pipeline: one agent does the whole task.
ai-team carries no IDE-, tool- or service-specific logic: "fix all WebStorm / Sonar / CI errors" goes to the agent like any task.

## 3. Provider choice (capability first, then cost)
Expected cost = (observed baseline + our prompt + 0.1×cached + 4×output×effort + 30×seconds) ÷ p (read-only) or ÷ p² (edits).
p = tier prior × role capability, lowered when demand exceeds the tier, blended with routing history (prior 8 samples for
success, 5 for cost; weak history cannot override priors). Eligible = within 0.10 (edits) or 0.25 (others) of the best p;
the cheapest eligible wins. Unavailable, broken or rate-limited providers are never picked.

## 4. The prompt
The original task, untouched, plus a few router lines: non-interactive, permissions (read-only; or don't commit/push and never
edit generated output) and a one-line JSON status contract. No project dump, role essay or workflow text: each CLI loads its
own instructions and selects its native tools. Browser, computer, MCP, plugins, skills and user settings are inherited;
ai-team does not disable capabilities to reduce prompt tokens or force a writer sandbox/permission mode.
Read-only roles retain safety constraints: Codex read-only sandbox; Claude non-interactive read permissions with file
mutation tools denied. General answers inherit native permissions. Fresh session per call; stored logs are redacted.


## 5. Failure handling
rate limit → cooldown until the CLI's reported reset (default 60 min), next provider · transient (5xx, overloaded, network)
→ one retry with backoff (retry-after, else 1 s doubling, ≤ 30 s) · auth/CLI error → provider off for the run · budget/turn
limit or timeout → next provider for that call · agent answers blocked/failed → next provider, then one tier up.
Failed checks go back to the agent with their output: at most 2 attempts per tier (T3: 1), then one tier up; the same failure
fingerprint three times → stop (REPEATED_FAILURE). An edit task with no change and no status line is NOT DONE, never DONE.
BLOCKED only when no provider can take the call, or the agent needs information only the user has.

## 6. Verification (T0, after edits)
Only explicitly repository-related tasks use repository verification, generated-output protection and diff review.
Non-project work succeeds from the native agent's result, without requiring a Git diff. The project's own check commands for the changed files — never a mutating one (`--fix`, `--write`, `-u`, tsc without
`--noEmit`, or a script that calls them). nothing changed → none · docs/images → none · CSS → lint · copy JSON → parse ·
code MICRO/SMALL → typecheck (+ a sibling test) · MEDIUM+ → typecheck + lint + tests · config → + build · CRITICAL → + build.
Baseline-aware: failures that existed before the task don't fail it; a check the task is about ("fix the failing tests",
"fix all errors") must pass outright.

## 7. Review only where it pays
balanced: CRITICAL tasks or changes touching auth/security/payment/migration code · quality: every code change · economy: never.
One independent reviewer (the other provider when available); blocking issues get one fix round, then the checks run again.

## 8. Safety and state
Generated output (build dirs with evidence: git-ignored or named as output in build config) is snapshotted before each writing
call and restored after; only source is fixed. The user's uncommitted work is never reverted. State: last 30 task dirs /
14 days; metrics (routing history, tokens, cost) rotate at 1000 runs and store numbers only.

## 9. Canonical capabilities
`capabilities.json` lists owned runtime files, not an alternative routing policy. Role files serve only the interactive
entrypoints (`/team`, Claude subagents, Codex `team`); routed runs send the original task. Optional Graphify: one prompt line
only with an installed CLI and a valid local graph; ai-team never installs or rebuilds graphs.
