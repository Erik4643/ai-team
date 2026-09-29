"""CI checks. Remote pipeline results are not readable from a working copy: CI evidence is a supplied job log / report
(text, JUnit, SARIF), or the pipeline's own check steps re-run locally when they map to known read-only checks.
Build, deploy, install and push steps never run."""
import os
import re

from .core import CHECK_NAME, READ_ONLY_FORMS, Adapter, cmd_tokens, not_configured, read_only_form, resolve_command, runnable, unavailable
from .scripts import ADAPTERS as SCRIPT_CHECKS

CI_FILES = (".gitlab-ci.yml", ".gitlab-ci.yaml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".circleci/config.yml", ".travis.yml")
TEST_RUNNERS = ("jest", "vitest", "mocha", "pytest", "ava")
CHECK_SCRIPTS = {n for a in SCRIPT_CHECKS if a.id != "BUILD" for n in a.scripts}


def ci_files(root):
    files = [p for p in CI_FILES if (root / p).is_file()]
    wf = root / ".github" / "workflows"
    return files + (sorted(str(p.relative_to(root)) for p in wf.glob("*.y*ml")) if wf.is_dir() else [])


def step_commands(text):
    """`script:` items (GitLab and most YAML CIs) and `run:` values (GitHub Actions), including block scalars.
    No YAML engine: only these two keys are read."""
    out, indent = [], None
    for line in text.splitlines():
        m = re.match(r"^(\s*)(?:-\s+)?(script|run)\s*:\s*(.*?)\s*$", line)
        if m:
            value = m.group(3)
            indent = len(m.group(1)) if value in ("", "|", ">", "|-", ">-") else None
            if value.startswith("[") and value.endswith("]"):  # flow list: script: ["yarn lint", "yarn test"]
                out += [x.strip().strip("'\"") for x in value[1:-1].split(",") if x.strip()]
            elif indent is None and value:
                out.append(value.strip("'\""))
            continue
        if indent is not None and line.strip():
            depth = len(line) - len(line.lstrip())
            if depth > indent or (depth == indent and line.lstrip().startswith("- ")):  # `script:` items may share its indent
                out.append(re.sub(r"^-\s+", "", line.strip()).strip("'\""))
            else:
                indent = None
    return out


def local_check(cmd, ctx):
    """A CI step → its local read-only form when it is a known check (a package check script or an allowlisted checker),
    else None: builds, deploys, installs and arbitrary commands are never re-run."""
    toks = cmd_tokens(cmd)
    if not toks:
        return None
    scripts, execp = (ctx.pkg[0], ctx.pkg[2]) if ctx.pkg else ({}, "")
    rest = toks[1:] if toks[0] in ("npm", "yarn", "pnpm", "bun") else []
    if rest[:1] in (["run"], ["run-script"]):
        rest = rest[1:]
    name = rest[0] if rest and rest[0] in scripts else None
    resolved, _ = resolve_command(cmd, scripts)
    tool = os.path.basename(resolved[0]) if resolved else ""
    if (name and (name in CHECK_SCRIPTS or CHECK_NAME.search(name))) or (not name and (tool in READ_ONLY_FORMS or tool in TEST_RUNNERS)):
        return read_only_form(cmd, scripts, execp)["cmd"]
    return None


class CIChecks(Adapter):
    def detect(self, ctx, explicit):
        files = ci_files(ctx.root)
        if not files:
            return not_configured("no CI configuration")
        cmds, other = [], 0
        for f in files:
            for step in step_commands((ctx.root / f).read_text(errors="replace")):
                c = local_check(step, ctx)
                if c and c not in cmds:
                    cmds.append(c)
                elif not c:
                    other += 1
        if not cmds:
            return unavailable(f"{', '.join(files)}: no locally reproducible read-only check steps ({other} build/deploy/setup step(s)); "
                               "remote pipeline results are not readable here",
                               unblock="pass the failing job's log or report: ai-team --evidence CI_DIAGNOSTICS=<job.log | junit.xml | report.sarif> \"<task>\"")
        return runnable(cmds[0], cmds=cmds, confidence="medium", derivation_reason="CI check steps re-run locally",
                        configured_command=", ".join(files), note=f"{len(cmds)} check step(s) from {', '.join(files)} re-run locally; remote results not read")


ADAPTERS = [CIChecks("CI_DIAGNOSTICS", "ci", weak_names=(r"ci", r"ci/cd", r"pipelines?", r"github\s+actions", r"gitlab[- ]?ci",
                                                         r"merge\s+request\s+checks", r"pr\s+checks"), order=80)]
