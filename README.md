# AI team

A portable, stack-neutral task router for **Codex and Claude Code**. Python 3.9+ and Git are required; install and authenticate at least one provider CLI separately. No dependencies, sudo, vendor skill bundles or provider credentials are installed by this kit.

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
ai-team "<anything>"           # the only command you need
ai-team --plan "task"          # routing decision, no model calls
ai-team --context-plan "task"  # the exact prompt the agent would get, no model calls
ai-team --budget economy|balanced|quality "task"
ai-team --cost
ai-team --status
ai-team --doctor
ai-team --self-test
ai-team --version
ai-team update                 # explicit fetch + fast-forward + validated install
```

Update refuses a dirty working tree, detached HEAD or missing upstream. It does not overwrite local changes and never touches the current project. After an update that changed the kit, refresh each project with `ai-team init` (idempotent; project facts are preserved). Doctor, tests and installation do not call models. `--self-test --live` is an explicit opt-in to real calls.

## Runtime design

ai-team is a router, not a problem-solving framework:

1. **Classify** (T0, 0 tokens): task type, complexity, estimated context size, and whether it needs edits or web research. Status, project facts, git inspection, running a project command and reviewing a clean tree are answered locally.
2. **Choose** Codex or Claude Code and the cheapest capable tier from the cost model and routing history. Edits start at T1 (small) or T2 (medium and larger, or large context); T3 is reached only by escalation.
3. **Delegate** the original task, plus a few router lines (permissions and a one-line status contract), to the native CLI. The agent loads its own project instructions and solves the task with its own tools with its configured permissions. One agent does the whole task — no explorer/architect/reviewer pipeline.
4. **Recover**: a rate limit cools the provider down until its reported reset and hands the call to the other provider; a transient error is retried once with backoff; an auth or CLI error takes the provider out for the run; an agent that gives up hands over to the other provider, then one tier up. BLOCKED appears only when no provider can continue or the agent needs information from you.

For repository editing tasks, the project's own read-only checks run for the changed files (baseline-aware; never `--fix`/`--write`), and a failure goes back to the agent. Changes touching auth/security/payment/migration code, or CRITICAL tasks, get one independent review (every code change with `--budget quality`, none with `economy`). Edits to generated build output are reverted; the user's uncommitted work is never touched. Routing history, tokens and cost are recorded locally (`ai-team --cost`); `Fallbacks:` in the summary and `route.json` in the task state show what happened.

AI-Team routes any task, including research, computer troubleshooting, configuration and general investigation. Native browser, computer, MCP, plugins and tools remain available; the selected CLI decides what it needs. Non-project tasks do not require Git changes or repository checks.

ai-team deliberately has no IDE-, linter-, CI- or service-specific logic: "fix all WebStorm errors" or "fix the Sonar issues" goes to the agent, which inspects the project with its own tools. [Policy](POLICY.md) has the exact rules; [canonical capabilities](docs/CANONICAL_CAPABILITIES.md) lists the 8 runtime capabilities. [Skill audit summary](docs/SKILL_INVENTORY.md) explains why the historical 4,661 definitions are not the runtime set. Graphify is optional and uses existing project graphs; `AI_TEAM_GRAPHIFY=off` disables it.

| Path | Ownership |
|---|---|
| `bin/ai-team`, `scripts/` | global router, bootstrap, installation, update and health |
| `routing.json` | model mappings, start tiers, budgets, cost priors and provider routing |
| `POLICY.md` | single global policy |
| `roles/`, `skills/graphify/` | guidance for the interactive entrypoints (`/team`, subagents); routed runs send the original task |
| `capabilities.json` | runtime capability allowlist |
| `template/`, `tests/` | generic templates and zero-model regression tests |
| `state/` | ignored global metrics, cooldowns and health state |
| `<project>/.ai/` | project facts, map, decisions, migration backup and task state |

Only Codex and Claude are configured. Other providers require future explicit integration; no third-provider files are generated. [Migration and portability](docs/PORTABILITY.md) describes boundaries and limitations. The current version is a pre-release; provider authentication and live task success are not certified by deterministic tests.
