# ai-team policy (read only when orchestrating; `ai-team` applies it deterministically from `routing.json`)

## 1. Intent first (T0, 0 tokens)
META / project facts (commands, stack) / INSPECTION (git status, diff) / TOOL (run lint, tests, build) → local, 0 model calls.
QUESTION → T1 read-only (repo cues → explorer with tools; general → answer, no tools). UNKNOWN → T1 probe, edits only if it finds a change.
REVIEW → reviewer on the current diff, no implementer. RESEARCH → researcher only. PLAN → explorer (+ architect if complex), no edits.
IMPLEMENTATION / DEBUG / ARCHITECTURE → complexity below. ARCHITECTURE stops at the decision unless `--apply`.

## 2. Complexity → dynamic DAG (plan-time tiers never include T3)
| Class | Stages |
|---|---|
| MICRO (rename, typo, color, radius, label, wording; ≤2 paths; cosmetic) | one T1 editor → checks for the changed file types |
| SMALL | one T1 editor → targeted checks |
| MEDIUM | explorer T1 (skipped if paths given) → editor T2 → [reviewer if the diff warrants it] |
| COMPLEX | explorer T1 → [architect T2 if evidence] → editor T2 → verify → [reviewer if risk] |
| CRITICAL (needs repo evidence) | explorer → architect T2 → editor T2 → verify incl. build → reviewer T2, independent vendor |
"Fix all errors" → T0 diagnostics grouped by file → bounded editor → the diagnostic re-runs as the acceptance check.
Explorer evidence can reclassify MICRO..COMPLEX (down as well as up); CRITICAL is never entered or left automatically.

## 3. Gates (evidence, not keywords)
Architect skipped when the explorer reports a localized root cause: confidence ≥ 0.80 (≥ 0.90 for auth, security, payments,
migrations, destructive ops, concurrency), ≤ 3 files, 1 subsystem, no architecture decision, no open questions, risk not high.
Researcher only if the task asks for current external facts or the explorer sets external_research_required.
Reviewer is decided after checks, from the diff: security/concurrency/public-API/config change, ≥ 60 lines, several modules,
implementer confidence < 0.7, code without a runnable check, checks that failed before passing, or files outside the plan.
Docs-only and small verified diffs skip review. Economy reviews only CRITICAL or security-touching diffs; quality reviews every code change.

## 4. Verification matrix (cheapest meaningful check)
nothing changed → none · docs/images → none · CSS → lint · copy JSON → JSON parse · code (MICRO/SMALL) → typecheck (+ tests if a
sibling test exists) · code (MEDIUM+ or ≥4 files) → typecheck + lint + tests · config → + lint + build · dependencies → typecheck + build ·
CRITICAL → + build. Checks the user names always run.

## 5. Providers (capability first, then cost)
Expected cost = (observed baseline + our context + 0.1×cached + 4×output×effort + 30×seconds) ÷ p (read-only) or ÷ p² (edits).
p = tier prior × capability (analysis, explore, edit, docs, architecture, review, debug, research), lowered when demand exceeds the
tier, blended with history (prior 8 samples for success, 5 for cost; intent-specific history only from 5 samples up).
Eligible = within 0.10 (edits) or 0.25 (others) of the best p; cheapest eligible wins. Unavailable, broken or rate-limited
providers (60 min cooldown) are never selected.

## 6. Failures and escalation (reason codes)
Same tier at most 2 attempts (T3: 1). Identical failure fingerprint three times → stop (REPEATED_FAILURE).
Codes: LOW_CONFIDENCE, MULTIPLE_ROOT_CAUSES, SECURITY_RISK, CONCURRENCY_AMBIGUITY, ARCHITECTURE_CHANGE, CONFLICTING_EVIDENCE,
FAILED_T1, FAILED_T2, INCOMPLETE_VERIFICATION. T3 only via: architect confidence < 0.6 on COMPLEX+, FAILED_T2, or a judge on
unresolved review disagreement (question + both positions + diff only).

## 7. Context
Each call: role (≤0.1k) + one-line output schema + project sections the role needs (Rules always; facts for explorer/architect/
MEDIUM+ editors; graphify only for explorer/architect) + repo map (explorer/architect only) + handoff (paths, never contents) + diff
(reviewer). Budgets (ours): MICRO 1.5k · SMALL 2k · MEDIUM 8k · COMPLEX 16k; over budget → drop map, project, docs, then trim diff.
Workers don't auto-load provider instructions (Codex project AGENTS.md off, Claude settings/skills/plugins off). Stable prefix order;
no timestamps or ids in prompts. Fresh sessions per call (no reuse across tasks). Secrets are redacted before anything is written.
State: last 30 task dirs / 14 days; metrics rotate at 1000 runs and store numbers only.

## 8. Evidence (T0, `diagnostics/`)
A source the task names (tool + findings word, or a fix verb: "Stylelint errors", "WebStorm inspection results", "CI failures"),
supplied with `--evidence`, or required by a project custom check is REQUIRED; broad maintenance adds the configured checks.
DONE only when every REQUIRED source was read clean; unreadable → BLOCKED before any model call, never inferred from other checks.
Only REAL_SOURCE findings go to an implementer (generated, third-party, IDE false positives, spelling, config noise, low value never do).

## 9. Canonical capability loading
`capabilities.json` lists owned runtime capabilities, not an alternative routing policy. Keep deterministic decisions in
`bin/ai-team` and `routing.json`. Load only the selected role and at most two matching supplemental workflows; drop supplemental
guidance first under context pressure. Load optional Graphify guidance only with an installed CLI and a valid local graph;
otherwise use targeted search. Queries do not authorize installation or graph rebuilds. Provider adapters load canonical
instructions on invocation. Vendor caches, archives and inventory reports are never runtime sources; project facts remain local.
