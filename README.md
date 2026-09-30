# AI team

A portable, stack-neutral development orchestrator for **Codex and Claude Code**. Python 3.9+ and Git are required; install and authenticate at least one provider CLI separately. No dependencies, sudo, vendor skill bundles or provider credentials are installed by this kit.

## Install

```bash
git clone https://github.com/Erik4643/ai-team.git ~/.ai-kit
cd ~/.ai-kit
./install.sh
```

The installer creates `~/.local/bin/ai-team` and `ai-init`, small global provider pointers and six thin provider entrypoints. It backs up changed global instructions outside the repository, preserves unrelated settings, and runs deterministic self-tests and doctor. If prompted, add `export PATH="$HOME/.local/bin:$PATH"` to your shell profile. Re-running installation is safe.

## Initialize a project

```bash
cd any-project
git init                 # only if not already a Git repository
ai-team init
```

This creates `.ai/CONTEXT.md`, a compact `repo-map.json`, `decisions.md`, ignored state/backups, and tiny `AGENTS.md` / `CLAUDE.md` adapters. `ai-init` remains an alias. It never wraps or changes Git.

Existing instructions are preserved locally, redacted, and migrated with backups. Recognized duplicate orchestration files are removed only after exact-content identification. Unique skills and provider settings remain. Strong migration snapshots existing provider configuration before replacements:

```bash
ai-team init --clean
```

Both modes preserve application source and are idempotent. Backups retain three generations. Unknown configurations are preserved rather than guessed to be obsolete; review migrated project context for relevance. Graphs and all project facts remain under the project.

## Use

```bash
ai-team "fix all WebStorm errors"
ai-team "implement this feature"
ai-team "fix this bug"
ai-team "review current changes"
ai-team --plan "task"          # routing, no model calls
ai-team --context-plan "task"  # context accounting, no model calls
ai-team --cost
ai-team --status
ai-team --doctor
ai-team --self-test
ai-team --version
ai-team update                 # explicit fetch + fast-forward + validated install
```

Update refuses a dirty working tree, detached HEAD or missing upstream. It does not overwrite local changes and never touches the current project. After an update that changed the kit, refresh each project with `ai-team init` (idempotent; project facts are preserved). Diagnostics, tests and installation do not call models. `--self-test --live` is an explicit opt-in to real calls.

## Runtime design

T0 handles triage, mapping, budgeting, verification and cost estimation. T1/T2 roles are selected by capability and expected cost; T3 is reserved for evidence-based escalation. Routing, cooldowns, baseline-aware checks, aggregate diagnostics and risk-based review remain deterministic. Review uses a fresh isolated session and is labeled cross-provider only when providers differ.

**Agents and fallback.** Work is delegated to the native Codex and Claude Code CLIs, which use their own tools; ai-team only sets the role's sandbox and reads the result, usage and cost. The provider and tier come from the cost model and routing history. If a provider fails, the call moves along a fallback chain (inspired by claude-code-router): a rate limit cools the provider down until its reported reset, a transient error is retried once with backoff, an auth or CLI error takes the provider out for the run, and an agent that gives up hands over to the other provider. Verification failures retry and then escalate a tier. BLOCKED appears only when no provider can continue or the agents need information from you; `Fallbacks:` in the summary and `route.json` in the task state show what happened.

**Diagnostics support normal tasks.** Missing IDE exports, tools or reports do not prevent a model from inspecting source and configuration and attempting the task. Available diagnostics localize repairs (zero tokens when everything readable is clean); verification follows implementation. A missing source is reported as not verified, never as proof that the IDE or remote CI is clean. Real check failures still matter, generated-file protection stays active, and existing user changes are preserved.

`--evidence SOURCE=PATH` remains optional for reports you already have (SARIF, JetBrains exports, JUnit, JSON or tool output). Diagnostic adapters and project `custom_checks` remain supported. No special syntax or exported evidence is required for ordinary development tasks.

Only the selected role and relevant guidance load. [Canonical capabilities](docs/CANONICAL_CAPABILITIES.md) lists the 15 capabilities. [Skill audit summary](docs/SKILL_INVENTORY.md) explains why the historical 4,661 definitions are not the runtime set. Graphify is optional and uses existing project graphs; `AI_TEAM_GRAPHIFY=off` disables it.

| Path | Ownership |
|---|---|
| `bin/ai-team`, `scripts/` | global orchestrator, bootstrap, installation, update and health |
| `routing.json` | model mappings, cost priors, gates, budgets and provider routing |
| `diagnostics/` | evidence registry and adapters: triage, read-only commands, parsers, finding classes |
| `POLICY.md`, `PROTOCOL.md` | single global policy and handoff contract |
| `roles/`, `workflows/`, `skills/graphify/` | canonical lazy reasoning guidance |
| `capabilities.json` | runtime capability allowlist |
| `template/`, `tests/` | generic templates and zero-model regression tests |
| `state/` | ignored global metrics, cooldowns and health state |
| `<project>/.ai/` | project facts, map, decisions, migration backup and task state |

Only Codex and Claude are configured. Other providers require future explicit integration; no third-provider files are generated. [Migration and portability](docs/PORTABILITY.md) describes boundaries and limitations. The current version is a pre-release; provider authentication and live task success are not certified by deterministic tests.
