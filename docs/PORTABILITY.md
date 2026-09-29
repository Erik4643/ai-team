# Ownership, migration and portability

The kit contains reusable tooling only. Application architecture, commands, source paths, graph data and decisions stay in `<project>/.ai/`. Global metrics and health state are ignored. Historical detailed inventories, private task prompts and prior package backups were moved outside the repository before packaging.

## Installation and update

Installation uses Python's standard library, shell and Git, without sudo or network package installation. Command links use the actual checkout location, including paths with spaces. Provider adapters contain format-specific metadata and load one canonical role on invocation. Model names live in `routing.json`.

Global instruction migration replaces known legacy orchestration lines or a managed block; unrelated preferences and provider settings are preserved. Replaced files are backed up under `~/.local/state/ai-team/backups/`, outside Git. Three global backup generations are retained. Custom adapter files and symlinks are preserved and reported. Installation does not touch authentication, plugins, MCP configuration or unrelated hooks.

Updates require a clean Git checkout on a branch with an upstream. They fetch and fast-forward, then execute the updated installer and validation. A failed post-update validation is reported; changes are not silently reset or discarded. Automatic updates and Git-template hooks are not installed.

## Project migration

`ai-team init` locates the Git root (or uses the current directory when no Git repository exists). It reads manifests and AI instructions only; application code is never executed for detection. Existing context sections, custom map commands/boundaries, decisions, unique skills and provider settings are retained. New context is short, and longer migrated instructions are stored as redacted local references.

Replaced root instructions and exact known duplicates are backed up in `.ai/migration-backup/` before changes. `--clean` additionally snapshots discovered existing AI configuration when migration changes are needed. It does not erase unknown user configurations. Only exact canonical or audited reusable-definition hashes qualify for removal; matching a filename or mentioning orchestration is insufficient. Backups retain three generations and are ignored, as is all `.ai/state/`.

External symlinks are never followed for writes; symlinked canonical state/context destinations require manual resolution. Root instruction symlinks may be backed up and replaced with ordinary pointers without changing their targets. Neither mode rewrites application code or changes Git history.

## Detection and diagnostics

Detection covers JavaScript/TypeScript frameworks when manifests identify them, Python, Go, Rust, Java/Kotlin, Swift, .NET, Ruby, PHP, Docker, Terraform, Kubernetes markers and bounded workspace directories. Unknown stacks initialize successfully with `stacks: ["unknown"]`. Detected commands are candidates, not a claim that dependencies are installed; repositories may supply custom commands in the map. Checks never run a mutating script: an existing read-only script wins, otherwise only allowlisted rewrites apply (eslint/stylelint `--fix` removed, prettier `--write` → `--check`, tsc `--noEmit`); chained or unknown mutating commands stay UNAVAILABLE. Broad maintenance requires typecheck and lint; other checks are optional, so an optional check without a read-only form is a warning, not a block. Diagnostic sources are adapters in `diagnostics/` (script checks, JetBrains/Qodana, CI; one module per source, discovered automatically) plus project-declared `custom_checks`. A source the task names is required: it is read or the task is BLOCKED with unblock steps (supplied SARIF / JetBrains export / JUnit / JSON / tool output also counts); it is never inferred from other checks. Build-output directories count as generated only with evidence (git-ignored or named as an output in build config); diagnostics there are reported as GENERATED/IDE_ONLY, never handed to an implementer, and agent edits to them are reverted.

Doctor validates installation, sources, routing, adapters, runtime permissions, links and the last deterministic self-test fingerprint. Missing provider CLIs/authentication or optional Graphify are deployment prerequisites/warnings, not an installer failure. Uninitialized application projects are reported without being modified. Default doctor never probes models. Explicit `doctor --probe` and `--self-test --live` make real provider calls.

## Release limits

The deterministic suite verifies routing and lifecycle behavior without provider authentication or real model calls. CLI flags and remote models can evolve; mappings are centralized, and live probes remain opt-in. Cross-platform behavior is designed for macOS and Linux; the recorded packaging run executes on macOS. Windows is not supported by the shell entrypoints. Migration preserves unknown rules rather than attempting semantic deletion; review project-specific references after a complex migration.
