"""Checks a project declares itself (package.json scripts or repo-map commands), run only in their read-only form."""
import shlex

from .core import Adapter, not_configured, read_only_form, runnable, tracked_css, unavailable


def _record(cmd, source, configured, form, note):
    return {"cmd": cmd, "source": source, "configured_command": configured, "derived_read_only_command": cmd if form.get("derived") else None,
            "derivation_reason": form["reason"], "confidence": form["confidence"], "note": note}


class ScriptCheck(Adapter):
    """Script-backed check: an existing read-only script wins, else the first script with an allowlisted read-only form,
    else a form derived from the tool's own configuration (`derive`), else not configured."""

    def __init__(self, id, category, scripts, derive=None, show=None, unconfigured="", **kw):
        super().__init__(id, category, **kw)
        self.scripts, self.derive, self.show, self.unconfigured = tuple(scripts), derive, show, unconfigured

    def from_scripts(self, ctx):
        """→ record (cmd, configured_command, derived_read_only_command, derivation_reason, confidence, note) or None."""
        if ctx.pkg:
            scripts, run, execp, _ = ctx.pkg
            best = None
            for n in self.scripts:
                if not scripts.get(n):
                    continue
                f = read_only_form(f"{run} {n}", scripts, execp)
                rec = _record(f["cmd"], f"script `{n}`", f"{run} {n}", f,
                              f"derived from `{n}`: {f['reason']}" if f["derived"] else f"script `{n}`" if f["cmd"] else f"`{n}` modifies files; {f['reason']}")
                rec["configured_script"] = scripts[n]
                if f["confidence"] == "exact":
                    return rec
                if best is None or (best["cmd"] is None and rec["cmd"]):
                    best = rec
            return best
        c = ctx.rmap.get("commands", {}).get(self.category)  # non-node projects: the repo map's commands
        if not c:
            return None
        f = read_only_form(c)
        return _record(f["cmd"], "repo-map", c, f, "" if f["confidence"] == "exact" else f["reason"])

    def detect(self, ctx, explicit):
        rec = self.from_scripts(ctx)
        if (rec is None or not rec["cmd"]) and self.derive:
            rec = self.derive(ctx, explicit) or rec
        if rec is None:
            return not_configured(self.unconfigured)
        fields = {k: rec.get(k) for k in ("configured_command", "derived_read_only_command", "derivation_reason", "confidence")}
        if not rec["cmd"]:
            return unavailable(rec["note"], unblock=f"add a read-only `{self.scripts[0]}` script, or declare a read-only custom check "
                                                    f"with \"source\": \"{self.id}\" in .ai/repo-map.json", **fields)
        return runnable(rec["cmd"], note=rec["note"] or rec["source"], **fields)


def _tsc(ctx, explicit):
    """TypeScript project without a typecheck script: `tsc --noEmit` (high confidence; also for broad maintenance)."""
    if ctx.pkg and "typescript" in ctx.pkg[3] and (ctx.root / "tsconfig.json").exists():
        c = f"{ctx.pkg[2]} tsc --noEmit"
        return {"cmd": c, "source": "derived (TypeScript project)", "configured_command": None, "derived_read_only_command": c,
                "derivation_reason": "typescript + tsconfig.json, no typecheck script", "confidence": "high", "note": "derived (TypeScript project)"}


def _from_config(tool, configs, args, reason):
    """Tool installed + its config file present, no script: a canonical read-only invocation. Only for explicit requests
    (medium confidence), never for broad maintenance, so routine runs only use what the project scripted."""
    def derive(ctx, explicit):
        if not explicit or not ctx.pkg or tool not in ctx.pkg[3] or not any(any(ctx.root.glob(g)) for g in configs):
            return None
        c = f"{ctx.pkg[2]} {tool} " + " ".join(shlex.quote(a) for a in args)
        return {"cmd": c, "source": f"{tool} config", "configured_command": None, "derived_read_only_command": c, "derivation_reason": reason,
                "confidence": "medium", "note": f"derived from {tool} config (explicit request): {reason}"}
    return derive


ADAPTERS = [
    ScriptCheck("TYPECHECK", "typecheck", ("typecheck", "type-check", "check-types", "types", "tsc"), derive=_tsc,
                names=(r"tsc", r"type-?check\w*", r"type\s+check\w*", r"типиз\w*"), weak_names=(r"typescript", r"ts", r"types?", r"typing"),
                maintenance="required", order=10),
    ScriptCheck("LINT", "lint", ("lint:check", "lint:ci", "check:lint", "lint", "eslint"),
                derive=_from_config("eslint", ("eslint.config.*", ".eslintrc*"), ["."], "eslint config present, no lint script"),
                names=(r"lint", r"linter", r"linting", r"lints", r"eslint", r"линт\w*"), maintenance="required", order=20),
    ScriptCheck("STYLELINT", "style", ("lint:css:check", "lint:css", "lint:style", "lint:styles", "lint:scss", "stylelint:check", "stylelint"),
                derive=_from_config("stylelint", (".stylelintrc*", "stylelint.config.*"), ["**/*.{css,scss,sass,less}", "--allow-empty-input"],
                                    "stylelint config present, no stylelint script"),
                show=lambda ctx: tracked_css(ctx.root), unconfigured="no stylelint script; source CSS is not linted",
                names=(r"stylelint", r"css[- ]?lint\w*", r"style[- ]?lint\w*"), maintenance="optional", order=30),
    ScriptCheck("TEST", "test", ("test:ci", "test:unit", "test"),
                weak_names=(r"tests?", r"specs?", r"unit tests?", r"test suite", r"jest", r"vitest", r"pytest", r"mocha", r"тест\w*"),
                maintenance="optional", order=40),
    ScriptCheck("FORMAT", "format", ("format:check", "prettier:check", "check:format", "check-format", "format-check", "lint:format", "prettier", "format"),
                derive=_from_config("prettier", (".prettierrc*", "prettier.config.*"), ["--check", "."], "prettier config present, no format script"),
                show=lambda ctx: False, names=(r"prettier", r"format(?:ting)?\s+check\w*", r"code\s+format\w*"), maintenance="optional", order=50),
    ScriptCheck("BUILD", "build", ("build",), weak_names=(r"build", r"compil\w*", r"bundl\w*", r"сборк\w*"), maintenance="deferred", order=60),
]
