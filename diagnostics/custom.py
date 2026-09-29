"""Project-declared evidence (.ai/repo-map.json → custom_checks) and generic discovery for sources without an adapter.

Only trusted project configuration runs, only in a read-only form, and a source the task names but nothing provides is
never fabricated from other checks. Example entry:

  {"id": "api-schema", "command": "yarn validate", "read_only": true, "required_for": ["api", "maintenance"],
   "aliases": ["schema validation"], "parser": "text"}

Optional keys: "source" (provide evidence for an existing id such as JETBRAINS_INSPECTION, or name a new one), "optional_for",
"parser" (text | sarif | jetbrains | json | junit), "output" (report file the command writes, repo-relative), "timeout_s"."""
import os
from pathlib import Path
import re

from .core import CHECK_NAME, Adapter, cmd_tokens, parse_evidence_text, parse_text, read_only_form, resolve_command, runnable, unavailable

CUSTOM_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}$")
SOURCE_ID = re.compile(r"^[A-Z][A-Z0-9_:-]{1,40}$")
TAG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
PARSERS = ("text", "sarif", "jetbrains", "json", "junit")


class CustomCheck(Adapter):
    """A read-only check the project declares. With "source" it provides (or overrides) that evidence id and keeps the
    built-in adapter's task terms and roles; otherwise it is CUSTOM:<id>, requested by id, aliases or tags."""

    def __init__(self, spec, base=None):
        own = tuple(re.escape(x) for x in [spec["id"]] + list(spec.get("aliases") or []))
        super().__init__(spec.get("source") or f"CUSTOM:{spec['id']}", spec["id"], names=own + (base.names if base else ()),
                         weak_names=base.weak_names if base else (), phrases=base.phrases if base else (),
                         maintenance=base.maintenance if base else None, accepts=base.accepts if base else (),
                         companions=base.companions if base else (), authorities=base.authorities if base else None,
                         order=base.order if base else 90)
        self.spec = spec
        self.required_for, self.optional_for = set(spec.get("required_for") or ()), set(spec.get("optional_for") or ())

    def need_for_tags(self, tags):
        return "required" if self.required_for & tags else "optional" if self.optional_for & tags else None

    def detect(self, ctx, explicit):
        s = self.spec
        return runnable(s["command"], output=s.get("output"), parser=s.get("parser", "text"), timeout=s.get("timeout_s"), confidence="exact",
                        configured_command=s["command"], derivation_reason="declared read-only in .ai/repo-map.json",
                        note=f"custom check `{s['id']}` (.ai/repo-map.json)")

    def parse(self, text, ctx=None, failed=False):
        if self.spec.get("parser", "text") != "text" and (text or "").strip():
            try:
                return parse_evidence_text(text, self.id)[1]
            except ValueError:
                pass
        return parse_text(text, self.id, failed)


def validate(spec, ctx, known=None):
    """One custom_checks entry → (CustomCheck | None, reason). Rules: id is a slug; `read_only` is literally true; `command`
    is ONE simple command (no shell operators, substitution or escapes) with no mutating flag; tags are slugs; the parser
    is known; `output` stays inside the repository. Invalid entries are reported and never run."""
    if not isinstance(spec, dict):
        return None, "entry is not an object"
    cid = spec.get("id")
    if not isinstance(cid, str) or not CUSTOM_ID.match(cid):
        return None, f"{cid!r}: id must be a lowercase slug"
    if spec.get("read_only") is not True:
        return None, f"{cid}: read_only must be true"
    cmd = spec.get("command")
    if not isinstance(cmd, str) or cmd_tokens(cmd) is None:
        return None, f"{cid}: command must be one simple command (no shell operators, substitution or escapes)"
    f = read_only_form(cmd, ctx.pkg[0] if ctx.pkg else {}, ctx.pkg[2] if ctx.pkg else "")
    if f["confidence"] != "exact":
        return None, f"{cid}: command is not read-only ({f['reason']})"
    for key in ("required_for", "optional_for", "aliases"):
        v = spec.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) and (TAG.match(x) if key != "aliases" else 0 < len(x) <= 60) for x in v):
            return None, f"{cid}: {key} must be a list of " + ("lowercase slugs" if key != "aliases" else "short strings")
    if spec.get("source") is not None and not (isinstance(spec["source"], str) and SOURCE_ID.match(spec["source"])):
        return None, f"{cid}: source must be an uppercase id such as JETBRAINS_INSPECTION"
    if spec.get("parser", "text") not in PARSERS:
        return None, f"{cid}: parser must be one of {', '.join(PARSERS)}"
    out = spec.get("output")
    if out is not None and (not isinstance(out, str) or os.path.isabs(out) or not (ctx.root / out).resolve().is_relative_to(ctx.root.resolve())):
        return None, f"{cid}: output must be a path inside the repository"
    t = spec.get("timeout_s")
    if t is not None and not (isinstance(t, int) and 0 < t <= 7200):
        return None, f"{cid}: timeout_s must be 1..7200"
    return CustomCheck(spec, (known or {}).get(spec.get("source"))), ""


def project_checks(ctx, known=None):
    """→ (valid CustomChecks, [(id, reason)] rejected) from the project's repo map; computed once per run."""
    if getattr(ctx, "_custom", None) is None:
        valid, rejected = [], []
        specs = ctx.rmap.get("custom_checks") or []
        for spec in specs if isinstance(specs, list) else [specs]:
            check, why = validate(spec, ctx, known)
            if check:
                valid.append(check)
            else:
                rejected.append((spec.get("id") if isinstance(spec, dict) else None, why))
        ctx._custom = (valid, rejected)
    return ctx._custom


def discover(name, ctx):
    """Generic discovery for a source with no adapter: a package.json script or Makefile target that the project named as
    a check (lint/check/verify/validate/audit/test/inspect/analyze) and that runs this tool — used in its read-only form.
    → availability or None. Nothing is invented, and tools are never run with guessed arguments."""
    stem = re.escape(name.lower().split("_")[0].split(":")[-1])
    tool_rx, name_rx = re.compile(stem + r"[\w.-]*", re.I), re.compile(r"(?<![a-z0-9])" + stem, re.I)
    if ctx.pkg:
        scripts, run, execp, _ = ctx.pkg
        for n in sorted(scripts):
            toks, _ = resolve_command(f"{run} {n}", scripts)
            tool = os.path.basename(toks[0]) if toks else ""
            if (tool_rx.fullmatch(tool) or name_rx.search(n)) and CHECK_NAME.search(n):
                f = read_only_form(f"{run} {n}", scripts, execp)
                if not f["cmd"]:
                    return unavailable(f"script `{n}` would modify files ({f['reason']})")
                return runnable(f["cmd"], confidence="medium", configured_command=f"{run} {n}", derivation_reason="discovered in package.json",
                                note=f"discovered: script `{n}` ({tool or name.lower()})")
    mk = Path(ctx.root) / "Makefile"
    if mk.is_file():
        for t in re.findall(r"^([A-Za-z][\w.-]*):", mk.read_text(errors="replace"), re.M):
            if name_rx.search(t) and CHECK_NAME.search(t):
                return runnable(f"make {t}", confidence="medium", configured_command=f"make {t}", derivation_reason="discovered in Makefile",
                                note=f"discovered: Makefile target `{t}`")
    return None


class MissingSource(Adapter):
    """A source the task names that no adapter or custom check provides. Satisfied only by supplied evidence or by generic
    discovery of a project-declared check; otherwise UNAVAILABLE (the task BLOCKS) — never by other checks' results."""

    def __init__(self, name):
        super().__init__(name, name.lower(), order=95)

    def detect(self, ctx, explicit):
        return discover(self.id, ctx) or unavailable(
            f"no adapter, custom check or project script provides {self.id} evidence",
            unblock=f"pass a report: ai-team --evidence {self.id}=<SARIF | JSON findings | tool output> \"<task>\", or declare a read-only "
                    f"custom check with \"source\": \"{self.id}\" in .ai/repo-map.json")
