"""Diagnostic evidence registry.

Adding a source means adding a module to this package that exports ADAPTERS (see core.Adapter); the orchestrator never
changes. Projects add read-only sources declaratively in .ai/repo-map.json → custom_checks (see custom.py).

Everything here is T0: the task text decides which sources are REQUIRED or OPTIONAL, and only the selected adapters probe
the project (Context.probed records which did). A REQUIRED source that cannot be read makes the task BLOCKED; it is never
replaced by other checks' results."""
import importlib
import pkgutil
import re

from . import core, custom
from .core import Adapter, Context, availability, not_configured, runnable, unavailable  # noqa: F401  (for adapter authors)

_REGISTRY, _LOADED = {}, [False]
BROAD = re.compile(r"\ball\b.{0,40}\b(errors|problems|issues|checks)\b(?!.{0,30}\b(type|lint|test|build))|\bevery\b|\bproject\b|все", re.I)
ALIASES = {"JETBRAINS": "JETBRAINS_INSPECTION", "WEBSTORM": "JETBRAINS_INSPECTION", "INTELLIJ": "JETBRAINS_INSPECTION", "IDEA": "JETBRAINS_INSPECTION",
           "IDE": "JETBRAINS_INSPECTION", "CI": "CI_DIAGNOSTICS", "ESLINT": "LINT", "TSC": "TYPECHECK", "TYPESCRIPT": "TYPECHECK",
           "PRETTIER": "FORMAT", "TESTS": "TEST", "CSS": "STYLELINT"}
KNOWN_UNSUPPORTED = (r"sonar\w*", "snyk", "semgrep", "codeql", "checkmarx", "lighthouse", "deepsource", "codacy", "codeclimate", "sentry",
                     "trivy", "bandit", "pylint", "flake8", "mypy", "rubocop", "clippy", "shellcheck", "hadolint", "markdownlint", "cspell",
                     "knip", "depcheck", "spotbugs", "pmd", "checkstyle", "detekt", "ktlint", "swiftlint", "golangci-lint")
PLURAL_FINDINGS = r"(?:issues|errors|warnings|findings|violations|alerts|problems|failures|reports|results|inspections|vulnerabilities|smells|hotspots)"
UNKNOWN = re.compile(r"(?<![\w./@-])([A-Z][\w-]*[A-Za-z0-9]|(?i:" + "|".join(KNOWN_UNSUPPORTED) + r"))"
                     r"(?=(?:\W+[\w-]+){0,2}?\W+(?i:" + PLURAL_FINDINGS + r")\b)")
STOP = set("""all any every each the these those this that real source project existing current remaining outstanding new known other
some many few open critical major minor high low medium type types runtime console compile compiler build test tests unit integration
e2e css scss html js ts jsx tsx json yaml xml sql api ui ux security performance accessibility a11y seo react vue angular next nuxt svelte
node nodejs typescript javascript python go rust java kotlin swift ruby php docker kubernetes k8s git github gitlab npm yarn pnpm ide
webpack vite babel i we fix please ai ci cd pr mr merge deploy prod production staging dev local remote server client frontend backend
database db network http https cors auth login import imports export exports module modules page pages component components hook hooks
style styles layout mobile desktop browser chrome safari firefox windows linux macos ios android user users admin""".split())


def register(adapter):
    """Add or replace an adapter at runtime (extensions, tests). No orchestrator change is needed."""
    adapters()
    _REGISTRY[adapter.id] = adapter
    return adapter


def unregister(adapter_id):
    _REGISTRY.pop(adapter_id, None)


def adapters():
    """id → adapter, discovered once from this package's modules. Importing an adapter module probes nothing."""
    if not _LOADED[0]:
        _LOADED[0] = True
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
            if info.name != "core" and not info.name.startswith("_"):
                for a in getattr(importlib.import_module(f"{__name__}.{info.name}"), "ADAPTERS", ()):
                    _REGISTRY.setdefault(a.id, a)
    return dict(sorted(_REGISTRY.items(), key=lambda kv: (kv[1].order, kv[0])))


def resolve_source(name):
    """User-facing source name (id, alias or tool name) → evidence id."""
    n = re.sub(r"[\s-]+", "_", str(name or "").strip()).upper()
    return n if n in adapters() else ALIASES.get(n, n)


def project_registry(ctx):
    """Global adapters plus the project's valid custom checks (a custom check with "source" overrides that id here)."""
    reg = adapters()
    if ctx is not None:
        for c in custom.project_checks(ctx, reg)[0]:
            reg[c.id] = c
    return reg


def unknown_sources(task, known):
    """Tool-like names in the task that ask for findings ("Sonar issues", "Snyk vulnerabilities") but that no adapter or
    custom check covers. They become REQUIRED sources without evidence — never substituted by other checks."""
    out, first, skip_until = [], re.match(r"\s*([\w-]+)", task or ""), -1
    for m in UNKNOWN.finditer(task or ""):
        tok = m.group(1)
        if m.start(1) < skip_until or tok.lower() in STOP or any(a.names_token(tok) for a in known):
            continue
        is_known = re.fullmatch("|".join(KNOWN_UNSUPPORTED), tok, re.I)
        if first and m.start(1) == first.start(1) and not is_known:
            continue  # a capitalised first word is just the start of a sentence
        tail = re.search(r"(?i:" + PLURAL_FINDINGS + r")\b", task[m.end(1):])
        skip_until = m.end(1) + (tail.end() if tail else 0)  # "Sonar Cloud issues" is one source, not two
        sid = re.sub(r"\W+", "_", tok).upper().strip("_")
        if sid not in out:
            out.append(sid)
    return out


def requested_ids(task, ctx=None):
    """Sources the task explicitly depends on (named near a findings word or after a fix verb, or an unknown tool asking
    for findings). T0: regexes only, no probing."""
    reg = project_registry(ctx)
    ids = [a.id for a in reg.values() if a.requested(task)]
    return ids + [u for u in unknown_sources(task, reg.values()) if u not in ids]


def select(task, ctx, maintenance=False, broad=False, supplied=()):
    """T0 triage → [(adapter, need, why, deferred)] in display order, and the set of explicitly required ids.
    Named in the task / evidence supplied / custom check required_for a task tag → REQUIRED (explicit). Broad maintenance
    adds each adapter's maintenance role (only when the task is broad or names nothing). Companions of a selected source
    join as OPTIONAL authorities."""
    reg = project_registry(ctx)
    picks, explicit = {}, set()

    def pick(a, need, why, deferred=False):
        cur = picks.get(a.id)
        if cur is None or (need == "required" and cur[1] != "required"):
            picks[a.id] = (a, need, why, deferred and need != "required")

    for a in reg.values():
        if a.requested(task):
            pick(a, "required", "named in the task"); explicit.add(a.id)
    for sid in supplied:
        a = reg.get(sid) or reg.setdefault(sid, custom.MissingSource(sid))
        pick(a, "required", "evidence supplied"); explicit.add(sid)
    tags = set(re.findall(r"[a-z0-9][a-z0-9_-]*", (task or "").lower())) | ({"maintenance"} if maintenance else set())
    for a in reg.values():
        need = a.need_for_tags(tags)
        if need:
            pick(a, need, "custom check for this task")
            if need == "required":
                explicit.add(a.id)
    for sid in unknown_sources(task, reg.values()):
        if sid not in picks:
            pick(custom.MissingSource(sid), "required", "named in the task; no adapter"); explicit.add(sid)
    if maintenance and (broad or not picks):
        for a in reg.values():
            if a.maintenance and a.id not in picks:
                pick(a, "required" if a.maintenance == "required" else "optional", "maintenance", deferred=a.maintenance == "deferred")
    for a, need, why, _ in list(picks.values()):
        for cid in a.companions:
            if cid in reg and cid not in picks:
                pick(reg[cid], "optional", f"authority for {a.id}")
    return sorted(picks.values(), key=lambda t: (t[0].order, t[0].id)), explicit


def attach(supplied, ctx, task):
    """--evidence entries → {source id: [paths]}. Unlabelled files are attached by what they are (SARIF tool, JetBrains
    export, JSON "source"), preferring a source the task requires or one that accepts it (JetBrains accepts Qodana SARIF)."""
    out = {}
    for sid, paths in (supplied or {}).items():
        for p in paths:
            if sid:
                out.setdefault(resolve_source(sid), []).append(p)
                continue
            try:
                found = core.load_evidence(p)[0]
            except (OSError, ValueError):
                found = ""
            want = requested_ids(task, ctx)
            reg = project_registry(ctx)
            target = next((i for i in want if i == found), None) or next((i for i in want if found in getattr(reg.get(i), "accepts", ())), None)
            out.setdefault(target or resolve_source(found) or (want[0] if len(want) == 1 else "EVIDENCE"), []).append(p)
    return out


def plan_rows(task, ctx, maintenance=False, broad=False, supplied=None):
    """Evidence plan: one row per selected source. state: run | deferred | unavailable | not configured; need: required |
    optional. An explicitly required source that is not configured is UNAVAILABLE (it cannot be proven clean); a
    maintenance default that is not configured is simply irrelevant to this project."""
    supplied = attach(supplied, ctx, task)
    selected, explicit = select(task, ctx, maintenance, broad, tuple(supplied))
    rows = []
    for a, need, why, deferred in selected:
        ctx.probed.append(a.id)
        av = a.detect(ctx, explicit=a.id in explicit)
        paths = supplied.get(a.id) or []
        for p in paths:  # supplied evidence must be readable now, or the source is unavailable
            try:
                core.load_evidence(p, a.id)
            except (OSError, ValueError) as e:
                av = core.unavailable(f"supplied evidence unreadable: {e}", unblock=f"--evidence {a.id}=<a readable SARIF / JetBrains export / JUnit / JSON / tool output>")
                paths = []
                break
        state = "run" if paths else av["state"]
        note = f"supplied evidence: {', '.join(str(p) for p in paths)}" if paths else av.get("note", "")
        if state == "not configured" and a.id in explicit:
            state, note = "unavailable", (av.get("note") or f"{a.id} is not set up in this project") + " — required by this task"
        if state == "not configured" and need == "optional" and getattr(a, "show", None) and not a.show(ctx):
            continue
        rows.append({"cat": a.category, "source": a.id, "need": need, "why": why, "state": "deferred" if state == "run" and deferred else state,
                     "cmd": av.get("cmd"), "cmds": av.get("cmds"), "output": av.get("output"), "parser": av.get("parser"),
                     "timeout": av.get("timeout"), "evidence": [str(p) for p in paths] or None, "static": bool(paths) and not av.get("cmd"),
                     "note": note, "unblock": av.get("unblock", ""), "configured_command": av.get("configured_command"),
                     "derived_read_only_command": av.get("derived_read_only_command"), "derivation_reason": av.get("derivation_reason"),
                     "confidence": av.get("confidence"), "fix": a.fix, "authorities": {k: sorted(v) for k, v in a.authorities.items()} or None,
                     "explicit": a.id in explicit})
    return rows
