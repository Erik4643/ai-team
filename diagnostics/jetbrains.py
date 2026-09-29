"""JetBrains inspections (WebStorm, IntelliJ IDEA, …) and Qodana.

The IDE keeps its inspection results in memory, so they are evidence only when exported from the IDE, produced by a
JetBrains CLI, declared by the project, or supplied — never inferred from tsc/eslint results. The headless `inspect.sh`
is only suggested: it cannot run while the IDE is open and re-indexes the whole project."""
from pathlib import Path
import shlex

from .core import Adapter, not_configured, runnable, unavailable

IDES = ("WebStorm", "IntelliJ IDEA", "IntelliJ IDEA Ultimate", "IntelliJ IDEA CE", "PhpStorm", "PyCharm", "PyCharm CE", "GoLand",
        "Rider", "RubyMine", "CLion")
# IDE inspections that re-check what the compiler / linter decides. When that authority runs clean in the same pass, the
# IDE finding is an IDE_FALSE_POSITIVE (stale index, path aliases, different config) — reported, never "fixed".
COMPILER_DUPLICATES = ("TypeScriptUnresolvedReference", "TypeScriptUnresolvedVariable", "TypeScriptUnresolvedFunction",
                       "TypeScriptValidateTypes", "TypeScriptValidateJSTypes", "TypeScriptCheckImport", "JSUnresolvedReference",
                       "JSUnresolvedVariable", "JSUnresolvedFunction")
LINTER_DUPLICATES = ("Eslint", "ESLint", "TsLint")


def inspect_sh():
    """Installed JetBrains IDE's headless inspector (macOS app bundles), for the unblock hint only."""
    for base in (Path("/Applications"), Path.home() / "Applications"):
        for ide in IDES:
            p = base / f"{ide}.app" / "Contents" / "bin" / "inspect.sh"
            if p.is_file():
                return p
    return None


class Qodana(Adapter):
    """qodana.yaml + Qodana CLI → a SARIF report written under the git-ignored state dir (a stale report is never read)."""

    def detect(self, ctx, explicit):
        cfg = next((n for n in ("qodana.yaml", "qodana.yml") if (ctx.root / n).is_file()), None)
        if not cfg:
            return not_configured("no qodana.yaml")
        if not ctx.which("qodana"):
            return unavailable(f"{cfg} is present but the Qodana CLI is not installed",
                               unblock="install the Qodana CLI (it runs its linter in Docker), or pass its report: --evidence QODANA=<qodana.sarif.json>")
        out = ctx.state_rel("qodana")
        return runnable(f"qodana scan --results-dir {shlex.quote(out)}", output=f"{out}/qodana.sarif.json", parser="sarif", timeout=3600,
                        confidence="high", configured_command=cfg, derivation_reason="qodana.yaml + Qodana CLI; results as SARIF",
                        note=f"Qodana CLI ({cfg}); SARIF report")


class JetBrainsInspection(Adapter):
    """Machine-readable JetBrains inspection evidence: Qodana when configured and installed; otherwise UNAVAILABLE with the
    exact ways to produce it (supplied exports and project-declared checks are attached by the registry)."""

    def detect(self, ctx, explicit):
        q = QODANA.detect(ctx, explicit)
        if q["state"] == "run":
            return dict(q, note="Qodana SARIF — JetBrains inspection engine; the qodana.yaml profile may differ from the IDE profile")
        facts = []
        if (ctx.root / ".idea").is_dir():
            profiles = sorted(p.stem for p in (ctx.root / ".idea" / "inspectionProfiles").glob("*.xml") if p.name != "profiles_settings.xml")
            facts.append(".idea present" + (f", profile {', '.join(profiles)}" if profiles else ""))
        if q["state"] == "unavailable":
            facts.append(q["note"])
        steps = ["export from the IDE: Code → Inspect Code… → results toolbar → Export (XML or JSON), then "
                 "ai-team --evidence JETBRAINS_INSPECTION=<export dir> \"<task>\""]
        ide = inspect_sh()
        if ide:
            steps.append(f"or headless with the IDE closed: {shlex.quote(str(ide))} \"$PWD\" .idea/inspectionProfiles/Project_Default.xml <out-dir> -format json")
        steps.append("or " + (q.get("unblock") or "add qodana.yaml and install the Qodana CLI"))
        steps.append("or declare a read-only custom check with \"source\": \"JETBRAINS_INSPECTION\" in .ai/repo-map.json")
        return unavailable("no machine-readable JetBrains inspection results — the IDE keeps them in memory"
                           + (f" ({'; '.join(facts)})" if facts else ""), unblock="\n".join(steps))


QODANA = Qodana("QODANA", "qodana", names=(r"qodana",), order=75)

ADAPTERS = [
    JetBrainsInspection("JETBRAINS_INSPECTION", "jetbrains",
                        phrases=(r"code\s+inspections?", r"inspect\s+code", r"ide\s+inspections?",
                                 r"inspections?\s+(?:results?|profiles?|warnings?|errors?|problems?|reports?)"),
                        weak_names=(r"webstorm", r"intellij(?:\s+idea)?", r"jetbrains", r"phpstorm", r"pycharm", r"rider", r"goland",
                                    r"rubymine", r"clion"),
                        accepts=("QODANA",), companions=("TYPECHECK", "LINT"),
                        authorities={"TYPECHECK": COMPILER_DUPLICATES, "LINT": LINTER_DUPLICATES}, order=70),
    QODANA,
]
