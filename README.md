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
ai-team "task"
ai-team --plan "task"          # routing, no model calls
ai-team --context-plan "task"  # context accounting, no model calls
ai-team --cost
ai-team --status
ai-team --doctor
ai-team --self-test
ai-team --version
ai-team update                 # explicit fetch + fast-forward + validated install
ai-team "fix Stylelint errors" # a named evidence source is REQUIRED: read and clean before DONE
ai-team --evidence JETBRAINS_INSPECTION=./inspections "fix WebStorm inspection warnings"
```

Update refuses a dirty working tree, detached HEAD or missing upstream. It does not overwrite local changes and never touches the current project. After an update that changed the kit, refresh each project with `ai-team init` (idempotent; project facts are preserved). Diagnostics, tests and installation do not call models. `--self-test --live` is an explicit opt-in to real calls.

## Runtime design

T0 handles triage, mapping, budgeting, verification and cost estimation. T1/T2 roles are selected by capability and expected cost; T3 is reserved for evidence-based escalation. Routing, cooldowns, baseline-aware checks, aggregate diagnostics and risk-based review remain deterministic. Review uses a fresh isolated session and is labeled cross-provider only when providers differ.

**Diagnostic evidence.** A task that names a source — TypeScript, ESLint, Stylelint, tests, build, WebStorm/JetBrains inspections, Qodana, CI, a project custom check, or an unknown tool such as Sonar — makes it REQUIRED: it is read (T0, read-only) and the task is DONE only when it is clean. If it cannot be read (WebStorm keeps its results inside the running IDE), the task is BLOCKED before any check or model call, with the exact way to unblock it: `--evidence SOURCE=PATH` accepts SARIF, a JetBrains XML/JSON export, JUnit, JSON findings or tool/CI output. Broad maintenance uses the project's configured checks and needs none of these. Findings are normalized and classified (real source, generated, third-party, IDE false positive, spelling, configuration noise, low value); only real source findings reach an implementer. A new source is one module in `diagnostics/` that exports `ADAPTERS`; projects declare read-only checks in `.ai/repo-map.json` → `custom_checks`.

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
