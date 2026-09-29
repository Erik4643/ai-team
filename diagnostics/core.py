"""Shared diagnostic-evidence primitives. Stdlib only; nothing here calls a model or runs a project command.

  commands  quote-aware parsing, script resolution, allowlisted read-only forms (a mutating check never runs)
  output    evidence-based generated / third-party classification of paths
  findings  one normalized schema for every source + parsers (tool text, SARIF, JetBrains XML/JSON, JUnit, JSON)
  classes   REAL_SOURCE_ERROR … OUT_OF_SCOPE; only real source findings are ever actionable
  adapters  Adapter base class, availability records and the lazy per-run Context
"""
from collections import Counter
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from urllib.parse import unquote
import xml.etree.ElementTree as ET


def sh(cmd, cwd=None, timeout=60):
    """List command → (returncode, stdout). Never reads stdin; any failure means "not available"."""
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


# ── commands: only allowlisted read-only forms ever run ────────────────────────
MUTATING = re.compile(r"(^|\s)(--fix\b|--fix-type\b|--write\b|-w\b|--update-?snapshots?\b|-u\b|--apply\b|--save\b)")
# The ONLY CLIs whose check commands are ever rewritten. drop = flag (+ n values) removed, swap = flag replaced,
# keep = flags that already make it read-only, add = appended when no keep/swap flag is present,
# reject = flags with no read-only equivalent. Any other command with a mutating flag stays UNAVAILABLE (never guessed).
READ_ONLY_FORMS = {
    "eslint":    {"drop": {"--fix": 0, "--fix-dry-run": 0, "--fix-type": 1}, "reason": "removed --fix: eslint reports without fixing"},
    "stylelint": {"drop": {"--fix": 0}, "reason": "removed --fix: stylelint reports without fixing"},
    "prettier":  {"swap": {"--write": "--check", "-w": "--check"}, "keep": {"--check", "-c", "--list-different", "-l"}, "add": "--check",
                  "reason": "--write → --check: prettier lists unformatted files and exits 1"},
    "tsc":       {"keep": {"--noEmit", "--noemit"}, "add": "--noEmit", "reject": {"-w", "--watch", "-b", "--build"},
                  "reason": "added --noEmit: type-check without emitting files"},
}
SHELL_UNSAFE = re.compile(r"[`$\\\n]")  # substitution, variables, escapes: never analysed, never derived
SCRIPT_RUN = {"npm": ("run", "run-script"), "pnpm": ("run",), "yarn": ("run",), "bun": ("run",)}
CHECK_NAME = re.compile(r"(^|[:_-])(check|lint|verify|validate|validation|audit|test|inspect|analy[sz]e)([:_-]|$)", re.I)


def cmd_tokens(cmd):
    """Quote-aware split of ONE simple command. → tokens, or None if it chains, pipes, redirects or substitutes."""
    if not cmd or SHELL_UNSAFE.search(cmd):
        return None
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        toks = list(lex)
    except ValueError:
        return None
    return None if not toks or any(set(t) <= set("();<>|&") for t in toks) else toks


def resolve_command(command, scripts, depth=0):
    """Follow `npm/yarn/pnpm/bun run <script>` references and `npx`/`<pm> exec`/`yarn <bin>` wrappers to the one tool that
    runs. → (tokens | None, text); tokens is None when the command (or a script it calls) is not one simple command."""
    toks = cmd_tokens(command)
    if toks is None or depth > 4:
        return None, command
    head, rest = toks[0], toks[1:]
    if head in ("npx", "bunx"):
        return (rest, command) if rest and not rest[0].startswith("-") else (None, command)
    if head not in SCRIPT_RUN or not rest:
        return toks, command
    if head in ("pnpm", "yarn") and rest[0] == "exec":
        return (rest[1:] or None), command
    explicit = rest[0] in SCRIPT_RUN[head]
    rest = rest[1:] if explicit else rest
    if not rest:
        return None, command
    name, extra = rest[0], rest[1:]
    if extra[:1] == ["--"]:
        extra = extra[1:]
    if name in scripts:
        return resolve_command(scripts[name] + (" " + shlex.join(extra) if extra else ""), scripts, depth + 1)
    return (None, command) if head == "npm" or (head == "bun" and not explicit) else (rest, command)  # yarn/pnpm <bin>


def read_only_args(tool, args):
    """Apply READ_ONLY_FORMS[tool]. → (args | None, changed, reason)."""
    spec, out, skip, changed = READ_ONLY_FORMS[tool], [], 0, False
    flag = lambda a: a.split("=", 1)[0]  # noqa: E731
    if any(flag(a) in spec.get("reject", ()) for a in args):
        return None, False, f"{tool} {'/'.join(sorted(spec['reject']))} has no read-only equivalent"
    for a in args:
        if skip:
            skip -= 1
            continue
        if flag(a) in spec.get("drop", {}):
            changed, skip = True, (spec["drop"][flag(a)] if "=" not in a else 0)
            continue
        if flag(a) in spec.get("swap", {}):
            changed, a = True, spec["swap"][flag(a)]
        if a in spec.get("keep", ()) and a in out:
            continue
        out.append(a)
    if not changed and spec.get("add") and not any(flag(a) in spec.get("keep", ()) for a in out):
        out.append(spec["add"]); changed = True
    if any(MUTATING.search(a) for a in out if a.startswith("-")):
        return None, False, f"{tool}: a mutating flag remains after the allowlisted rewrite"
    return out, changed, spec["reason"]


def read_only_form(command, scripts=None, execp=""):
    """Deterministic read-only form of ONE check command. Only READ_ONLY_FORMS tools are rewritten; unknown semantics are
    never guessed. → {cmd (runnable) | None, derived, reason, confidence: exact (unchanged) | high (allowlisted) | none}"""
    toks, text = resolve_command(command, scripts or {})
    if toks is None:
        if MUTATING.search(text):
            return {"cmd": None, "derived": False, "confidence": "none",
                    "reason": "shell operators/substitution with a mutating flag: not derived"}
        return {"cmd": command, "derived": False, "confidence": "exact", "reason": "no mutating flag; runs as configured"}
    tool = os.path.basename(toks[0])
    if tool in READ_ONLY_FORMS:
        args, changed, why = read_only_args(tool, toks[1:])
        if args is None:
            return {"cmd": None, "derived": False, "confidence": "none", "reason": why}
        if not changed:
            return {"cmd": command, "derived": False, "confidence": "exact", "reason": f"{tool} is already read-only"}
        return {"cmd": (execp + " " if execp else "") + shlex.join([toks[0]] + args), "derived": True, "confidence": "high", "reason": why}
    if any(MUTATING.search(t) for t in toks[1:] if t.startswith("-")):
        return {"cmd": None, "derived": False, "confidence": "none",
                "reason": f"`{tool}` has a mutating flag and is not in the read-only allowlist: not guessed"}
    return {"cmd": command, "derived": False, "confidence": "exact", "reason": "no mutating flag; runs as configured"}


def package_scripts(root, rmap):
    """→ (scripts, run prefix, exec prefix, deps) from package.json, or None without one."""
    pj = Path(root) / "package.json"
    if not pj.exists():
        return None
    try:
        pkg = json.loads(pj.read_text())
    except (OSError, ValueError):
        pkg = {}
    pkg = pkg if isinstance(pkg, dict) else {}
    pm = (rmap or {}).get("package_manager") or "npm"
    return (pkg.get("scripts") or {}, {"yarn": "yarn", "pnpm": "pnpm", "bun": "bun run"}.get(pm, "npm run"),
            {"yarn": "yarn", "pnpm": "pnpm exec", "bun": "bunx"}.get(pm, "npx"), {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})})


def tracked_css(root):
    return bool(sh(["git", "ls-files", "--", "*.css", "*.scss", "*.sass", "*.less"], cwd=root)[1].strip())


# ── output: generated and third-party code is never a fix target ──────────────
OUTPUT_DIR = re.compile(r"^(dist|build|out|output|coverage|storybook-static|target|\.next|\.nuxt|\.output|\.svelte-kit|\.turbo|\.vite)([-_.][\w.-]+)?$")
OUTPUT_KEY = re.compile(r"out-?dir|outputpath|output|dest\b|distdir|builddir|--out\b|\s-o\s", re.I)
THIRD_PARTY = re.compile(r"(^|/)(node_modules|bower_components|vendor|third[_-]?party|\.yarn|\.pnpm-store|site-packages)(/|$)")


def generated_dirs(root):
    """Top-level build/test output dirs. Name pattern AND evidence (git-ignored, or named as an output in package.json /
    build config) — a tracked source dir that merely looks like `build/` is not generated. → {dir: evidence}"""
    root = Path(root)
    try:
        cands = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and OUTPUT_DIR.match(p.name))
    except OSError:
        return {}
    if not cands:
        return {}
    _, ign = sh(["git", "check-ignore", "--"] + [c + "/" for c in cands], cwd=root)
    ignored = {x.strip().rstrip("/") for x in ign.splitlines()}
    cfg = []
    for p in [root / "package.json", root / "angular.json"] + sorted(root.glob("*.config.*")) + sorted(root.glob("tsconfig*.json")):
        try:
            if p.is_file() and p.stat().st_size < 200_000:
                cfg += [(p.name, line) for line in p.read_text(errors="replace").splitlines() if OUTPUT_KEY.search(line)]
        except OSError:
            continue
    out = {}
    for c in cands:
        # A path literal, not a bare word (`build: {` is a config key): "dist" / './dist/x', `dist-${mode}`, --outDir dist
        pre = re.match(r"^([a-z]+[-_.])\w", c)  # `dist-${mode}` names dist-dev, dist-master, …
        lit = re.compile(r"[\"'`](?:\./)?" + re.escape(c) + r"(?=[/\"'`])|(?:--out-?dir|--outDir|--output(?:-path)?|--dest|\s-o)[= ](?:\./)?"
                         + re.escape(c) + r"(?![\w-])" + (r"|[\"'`](?:\./)?" + re.escape(pre.group(1)) + r"\$\{" if pre else ""))
        ev = (["git-ignored"] if c in ignored else []) + sorted({f"output in {name}" for name, line in cfg if lit.search(line)})
        if ev:
            out[c] = ", ".join(ev)
    return out


def generated_classifier(root, dirs=None):
    """→ is_generated(path) for diagnostics paths (relative or absolute). True for evidence-based output dirs, vendor code
    (node_modules), and git-ignored files under an output-named directory at any depth. `.dirs` holds the evidence."""
    root = Path(root)
    dirs = generated_dirs(root) if dirs is None else dirs
    bases, memo = {str(root) + os.sep, str(root.resolve()) + os.sep}, {}

    def is_gen(path):
        p = str(path).strip()
        for base in bases:
            if p.startswith(base):
                p = p[len(base):]
                break
        if p not in memo:
            parts = Path(p).parts
            memo[p] = bool(parts) and (parts[0] in dirs or "node_modules" in parts or (
                any(OUTPUT_DIR.match(x) for x in parts[:-1]) and not os.path.isabs(p)
                and sh(["git", "check-ignore", "-q", "--", p], cwd=root)[0] == 0))
        return memo[p]
    is_gen.dirs = dirs
    return is_gen


# ── findings: one schema for every source ─────────────────────────────────────
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
DIAG_RE = re.compile(r"^(?P<file>[\w./@\[\]-]+\.\w{1,5})(?:[(:](?P<line>\d+)(?:[,:](?P<col>\d+))?\)?)?:?\s*-?\s*(?P<sev>error|warning)\s*"
                     r"(?P<code>[A-Z]{1,6}\d+)?:?\s*(?P<msg>.*)$", re.I)
ESLINT_ROW = re.compile(r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<sev>error|warning|✖|⚠)\s+(?P<msg>.*?)(?:\s{2,}(?P<code>[@\w/-]+))?$")
FILE_LINE = re.compile(r"^(?:[\w./@\[\]-]+|/[^:]*?)\.\w{1,5}$")  # eslint/stylelint file header (relative or absolute)
NOISE_RE = re.compile(r"^(found \d+ errors?|error command failed|info visit|done in|\$ |yarn run|npm err|✖ \d+ problems?|\d+ problems?|checking formatting|all matched files use|> )", re.I)
BROKEN_CHECK_RE = re.compile(r"no files matching the pattern|no inputs were found|no test files found|no tests found|missing script|command not found|"
                             r"cannot find module|could not find (a )?(config|configuration)|couldn't find a configuration", re.I)
SEVERITY = {"error": "error", "err": "error", "fatal": "error", "✖": "error", "critical": "error", "blocker": "error", "failure": "error",
            "warning": "warning", "warn": "warning", "⚠": "warning", "server problem": "warning",
            "weak warning": "info", "info": "info", "information": "info", "note": "info", "none": "info", "hint": "info",
            "typo": "typo", "grammar error": "typo"}
MAX_FINDINGS = 20000
EVIDENCE_LIMIT = 64 << 20


def norm_msg(t):
    t = ANSI_RE.sub("", t)
    t = re.sub(r"(/private)?/(tmp|var/folders)/[^\s:'\"]+", "<tmp>", t)
    t = re.sub(r"\b\d{1,2}:\d{2}(:\d{2})?(\.\d+)?\b|\b\d+(\.\d+)?\s?m?s\b", "", t)  # timestamps, durations
    return re.sub(r"\s+", " ", t).strip().lower()


def diagnostics(output):
    """Tool text → Counter{fingerprint: n} and {fingerprint: raw line} for baseline comparison. Fingerprint = file|code|message;
    line/column numbers and ordering are ignored (they shift with unrelated edits)."""
    fps, raw, cur = Counter(), {}, None
    for line in ANSI_RE.sub("", output or "").splitlines():
        m = DIAG_RE.match(line.strip())
        if m and not m.group("file").startswith(("node_modules", "_next")):
            fp = f"{m.group('file')}|{(m.group('code') or '').upper()}|{norm_msg(m.group('msg'))}"
        elif FILE_LINE.match(line.strip()):
            cur = line.strip(); continue
        elif cur and ESLINT_ROW.match(line):
            e = ESLINT_ROW.match(line)
            fp = f"{cur}|{e.group('code') or ''}|{norm_msg(e.group('msg'))}"
        else:
            continue
        fps[fp] += 1; raw.setdefault(fp, line.strip()[:220])
    if not fps:  # unparseable output: compare normalized non-noise lines, digits masked
        for line in (output or "").splitlines():
            t = re.sub(r"\d+", "#", norm_msg(line))
            if t and not NOISE_RE.match(norm_msg(line)):
                fps[t] += 1; raw.setdefault(t, line.strip()[:220])
    return fps, raw


def _int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def severity(raw):
    return SEVERITY.get(str(raw or "").strip().lower().replace("_", " "), "warning")


def finding(source, sev, file=None, line=None, column=None, code="", message="", raw="", fingerprint=None, fallback=False):
    """One normalized diagnostic: source, severity (error|warning|info|typo), file, line, column, code, message, fingerprint.
    classify() adds class, generated and third_party."""
    msg = re.sub(r"\s+", " ", str(message or "")).strip()
    f = {"source": source, "severity": severity(sev), "file": file or None, "line": _int(line), "column": _int(column),
         "code": str(code or ""), "message": msg[:500], "raw": str(raw or msg)[:220]}
    f["fingerprint"] = fingerprint or f"{f['file'] or ''}|{f['code']}|{norm_msg(msg)}"
    if fallback:
        f["fallback"] = True
    return f


def parse_text(output, source="", failed=False):
    """Structured tool lines (tsc, eslint/stylelint stylish, `file:line:col: severity …`) → findings. If a command failed and
    nothing parsed, each meaningful line becomes one unlocated error, so a failure can never read as clean."""
    out, cur = [], None
    for line in ANSI_RE.sub("", output or "").splitlines():
        s = line.strip()
        m = DIAG_RE.match(s)
        if m:
            out.append(finding(source, m.group("sev"), m.group("file"), m.group("line"), m.group("col"),
                               (m.group("code") or "").upper(), m.group("msg"), s))
        elif FILE_LINE.match(s):
            cur = s
        elif ESLINT_ROW.match(line):
            e = ESLINT_ROW.match(line)
            out.append(finding(source, e.group("sev"), cur, e.group("line"), e.group("col"), e.group("code") or "", e.group("msg"), s))
        if len(out) >= MAX_FINDINGS:
            break
    if not out and failed:
        for line in (output or "").splitlines():
            t = norm_msg(line)
            if t and not NOISE_RE.match(t):
                out.append(finding(source, "error", message=line.strip(), raw=line.strip(), fingerprint=re.sub(r"\d+", "#", t), fallback=True))
    return out[:MAX_FINDINGS]


def _path(value):
    """Report path → repo path: file:// URIs, JetBrains $PROJECT_DIR$ macros and ./ prefixes removed."""
    p = unquote(re.sub(r"^file://", "", str(value or "").strip()))
    p = p.replace("$PROJECT_DIR$/", "").replace("$PROJECT_DIR$", "")
    return p[2:] if p.startswith("./") else (p or None)


def _plain(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(html or ""))).strip()


def sarif_source(tool):
    t = str(tool or "").strip()
    if t.lower().startswith("qd") or "qodana" in t.lower():
        return "QODANA"
    return {"eslint": "LINT", "stylelint": "STYLELINT", "typescript": "TYPECHECK", "tsc": "TYPECHECK"}.get(t.lower(), re.sub(r"\W+", "_", t).upper().strip("_"))


def parse_sarif(data, source=""):
    """SARIF 2.x → findings. Suppressed / baseline-absent results are skipped; JetBrains' ideaSeverity wins over level."""
    out = []
    for run in data.get("runs") or []:
        driver = ((run or {}).get("tool") or {}).get("driver") or {}
        src = source or sarif_source(driver.get("name"))
        rules = {r.get("id"): r for r in driver.get("rules") or [] if isinstance(r, dict)}
        for res in (run or {}).get("results") or []:
            if not isinstance(res, dict) or res.get("suppressions") or res.get("baselineState") == "absent":
                continue
            loc = ((res.get("locations") or [{}])[0] or {}).get("physicalLocation") or {}
            region = loc.get("region") or {}
            rule = rules.get(res.get("ruleId")) or {}
            sev = ((res.get("properties") or {}).get("ideaSeverity") or res.get("level")
                   or (rule.get("defaultConfiguration") or {}).get("level") or "warning")
            out.append(finding(src, sev, _path((loc.get("artifactLocation") or {}).get("uri")), region.get("startLine"),
                               region.get("startColumn"), res.get("ruleId") or "", (res.get("message") or {}).get("text") or ""))
    return out[:MAX_FINDINGS]


def _xml(text):
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):  # stdlib only: no DTD/entities means no XXE and no entity expansion
        raise ValueError("XML evidence with a DTD/entities is not accepted")
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"unreadable XML: {e}")


def _jetbrains_xml(el, source, name=""):
    """JetBrains inspection export (IDE "Export to XML" / inspect.sh): <problems><problem><file/><line/><problem_class/>…"""
    out, code0 = [], Path(name).stem
    for pr in el.iter("problem"):
        pc = pr.find("problem_class")
        text = lambda tag: (pr.findtext(tag) or "").strip()  # noqa: E731
        code = (pc.get("id") if pc is not None and pc.get("id") else code0) or ((pc.text or "").strip() if pc is not None else "")
        out.append(finding(source, pc.get("severity") if pc is not None else "warning", _path(text("file")), text("line"), None,
                           code, _plain(text("description")) or ((pc.text or "") if pc is not None else "")))
    return out


def _jetbrains_json(data, source, name=""):
    """JetBrains inspection export in JSON (inspect.sh -format json): {"problems"|"listProblems": [{file, line, problem_class, description}]}"""
    out, code0 = [], Path(name).stem
    for pr in data.get("listProblems") or data.get("problems") or []:
        if not isinstance(pr, dict):
            continue
        pc = pr.get("problem_class") if isinstance(pr.get("problem_class"), dict) else {}
        out.append(finding(source, pc.get("severity") or pr.get("severity") or "warning", _path(pr.get("file")), pr.get("line"),
                           pr.get("column"), pc.get("id") or code0 or pc.get("name") or "", _plain(pr.get("description")) or pc.get("name") or ""))
    return out


def _junit(el, source):
    """JUnit XML (CI test reports): every failed/errored testcase is one error."""
    out = []
    for tc in el.iter("testcase"):
        for bad in list(tc.findall("failure")) + list(tc.findall("error")):
            name = ".".join(x for x in (tc.get("classname"), tc.get("name")) if x)
            out.append(finding(source, "error", tc.get("file"), tc.get("line"), None, bad.tag,
                               f"{name}: {bad.get('message') or (bad.text or '').strip()[:300]}"))
    return out


def _json_findings(data, source):
    items = data if isinstance(data, list) else next((data.get(k) for k in ("diagnostics", "findings", "problems", "issues")
                                                      if isinstance(data.get(k), list)), None)
    if items is None:
        raise ValueError("JSON evidence must be SARIF, a JetBrains export, or {\"diagnostics\": [...]} / a list of findings")
    out = []
    for it in items[:MAX_FINDINGS]:
        if isinstance(it, dict):
            out.append(finding(it.get("source") or source, it.get("severity") or it.get("level") or "warning",
                               _path(it.get("file") or it.get("path")), it.get("line"), it.get("column") or it.get("col"),
                               it.get("code") or it.get("rule") or it.get("ruleId") or "", it.get("message") or it.get("description") or ""))
    return out


def parse_evidence_text(text, source="", name=""):
    """Evidence content → (detected source, findings, format). Unreadable or unrecognised evidence raises ValueError:
    it never counts as clean."""
    s = (text or "").strip()
    if not s:
        raise ValueError("empty evidence")
    if s[0] in "[{":
        try:
            data = json.loads(s)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("runs"), list):
            tools = [((r or {}).get("tool") or {}).get("driver", {}).get("name") for r in data["runs"]]
            src = source or (sarif_source(tools[0]) if tools else "SARIF")
            return src, parse_sarif(data, source), "sarif"
        if isinstance(data, dict) and (isinstance(data.get("listProblems"), list) or (isinstance(data.get("problems"), list) and any(
                isinstance(p, dict) and "problem_class" in p for p in data["problems"]))):
            return source or "JETBRAINS_INSPECTION", _jetbrains_json(data, source or "JETBRAINS_INSPECTION", name), "jetbrains"
        if data is not None:
            src = source or (data.get("source") if isinstance(data, dict) else "") or ""
            return src, _json_findings(data, src), "json"
    if s.startswith("<"):
        el = _xml(s)
        if el.tag == "problems":
            return source or "JETBRAINS_INSPECTION", _jetbrains_xml(el, source or "JETBRAINS_INSPECTION", name), "jetbrains"
        if el.tag in ("testsuites", "testsuite"):
            return source or "", _junit(el, source), "junit"
        raise ValueError(f"unsupported XML evidence <{el.tag}> (expected a JetBrains inspection export or JUnit)")
    found = parse_text(s, source)
    if not found:
        raise ValueError("no diagnostics recognised in text evidence; supply SARIF, a JetBrains export, JUnit or JSON findings")
    return source, found, "text"


def load_evidence(path, source=""):
    """Supplied evidence file or directory → (detected source, findings, format). A directory is a JetBrains export
    (one XML/JSON file per inspection)."""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"evidence not found: {p}")
    if p.is_dir():
        files = sorted(x for x in p.rglob("*") if x.is_file() and x.suffix.lower() in (".xml", ".json"))
        if not files:
            raise ValueError(f"no inspection files (.xml/.json) in {p}")
        src, out = source or "JETBRAINS_INSPECTION", []
        for x in files:
            if x.name.startswith("."):  # .descriptions.xml
                continue
            _, found, _ = parse_evidence_text(x.read_text(errors="replace"), src, x.name)
            out += found
        return src, out[:MAX_FINDINGS], "jetbrains"
    if p.stat().st_size > EVIDENCE_LIMIT:
        raise ValueError(f"evidence larger than {EVIDENCE_LIMIT >> 20} MB: {p}")
    return parse_evidence_text(p.read_text(errors="replace"), source, p.name)


# ── classes: only real source findings are actionable ─────────────────────────
CLASSES = ("REAL_SOURCE_ERROR", "REAL_SOURCE_WARNING", "GENERATED_OUTPUT", "THIRD_PARTY", "IDE_FALSE_POSITIVE", "SPELLING",
           "CONFIGURATION_NOISE", "LOW_VALUE_WARNING", "OUT_OF_SCOPE")
SPELL = re.compile(r"spell|typo|grammar|cspell", re.I)
CONFIG_NOISE = re.compile(r"(^|/)(\.idea|\.vscode|\.qodana|\.fleet)(/|$)|\.iml$")


def relative(path, root):
    if not path or not root:
        return path
    for base in (str(root), str(Path(root).resolve())):
        if str(path).startswith(base + os.sep):
            return str(path)[len(base) + 1:]
    return path


def classify(f, is_gen=None, root=None, scope=(), duplicates=()):
    """Deterministic class for one finding (adds class, generated, third_party). Location first (third-party, generated,
    IDE config), then kind (spelling; a duplicate of a check that ran clean), then value (weak/info), then task scope;
    what remains is a real source error or warning."""
    rel = relative(f.get("file"), root)
    f["file"] = rel
    tp = bool(rel) and bool(THIRD_PARTY.search(rel) or rel.startswith("../") or os.path.isabs(rel))
    gen = bool(rel) and not tp and bool(is_gen and is_gen(rel))
    f["third_party"], f["generated"] = tp, gen
    if tp:
        c = "THIRD_PARTY"
    elif gen:
        c = "GENERATED_OUTPUT"
    elif rel and CONFIG_NOISE.search(rel):
        c = "CONFIGURATION_NOISE"
    elif f["severity"] == "typo" or SPELL.search(f.get("code") or ""):
        c = "SPELLING"
    elif f.get("code") in duplicates:
        c = "IDE_FALSE_POSITIVE"
    elif f["severity"] == "info":
        c = "LOW_VALUE_WARNING"
    elif scope and rel and not any(rel == s or rel.startswith(s.rstrip("/") + "/") for s in scope):
        c = "OUT_OF_SCOPE"
    else:
        c = "REAL_SOURCE_ERROR" if f["severity"] == "error" else "REAL_SOURCE_WARNING"
    f["class"] = c
    return f


def actionable(f, warnings=False, spelling=False):
    """Only real source findings ever reach an implementer; warnings when the task (or a failing check) asks for them."""
    c = f.get("class")
    return c == "REAL_SOURCE_ERROR" or (warnings and c == "REAL_SOURCE_WARNING") or (spelling and c == "SPELLING")


# ── adapters ──────────────────────────────────────────────────────────────────
FINDINGS = (r"(?:errors?|warnings?|issues?|problems?|findings?|violations?|failures?|failing|failed|fails|broken|diagnostics?|"
            r"inspections?|results?|reports?|alerts?|ошибк\w*|предупрежд\w*|проблем\w*)")
WEAK_FINDINGS = (r"(?:errors?|warnings?|issues?|problems?|findings?|violations?|failures?|failing|failed|fails|broken|diagnostics?|"
                 r"inspections?|pass|passes|passing|green|red|ошибк\w*|предупрежд\w*|проблем\w*)")
FIX_VERB = r"(?:fix|resolve|address|repair|clear|clean\s+up|eliminate|silence|исправ\w*|почин\w*|устрани\w*)"


def availability(state, **kw):
    """One adapter answer. state: run | not configured | unavailable. Optional keys: cmd (read-only command), cmds (several,
    run in order), output (report file the command writes), parser, timeout, confidence, note, unblock (what would make
    unavailable evidence readable), configured_command, derived_read_only_command, derivation_reason."""
    return dict({"state": state, "cmd": None, "note": "", "confidence": "none"}, **kw)


def runnable(cmd, **kw):
    return availability("run", cmd=cmd, **kw)


def unavailable(reason, unblock="", **kw):
    return availability("unavailable", note=reason, unblock=unblock, **kw)


def not_configured(reason="", **kw):
    return availability("not configured", note=reason, **kw)


class Adapter:
    """One diagnostic evidence source. Subclass and override detect(); parse() defaults to the generic text parser.

    id           stable source id shown to users and used in --evidence SOURCE=…
    category     short display/grouping name (legacy check categories: typecheck, lint, style, test, format, build)
    names        task terms that make the source explicitly required when near a findings word ("ESLint errors") or after
                 a fix verb ("fix eslint"); weak_names need a closer findings word ("TypeScript errors", not "TypeScript types");
                 phrases already ask for findings on their own ("inspection results")
    maintenance  role in broad maintenance: required | optional | deferred | None (only when a task asks for it)
    accepts      other detected evidence ids this source can use (JetBrains inspections accept Qodana SARIF)
    companions   sources run alongside as OPTIONAL authorities; authorities maps a companion to finding codes it settles
                 when it runs clean (tsc passing makes an IDE "unresolved type" an IDE_FALSE_POSITIVE)
    fix          source (actionable findings go to an implementer) | none (report only)
    verify       rerun (the command re-verifies fixes) | evidence (fixes need fresh evidence)"""

    def __init__(self, id, category, names=(), weak_names=(), phrases=(), maintenance=None, accepts=(), companions=(),
                 authorities=None, fix="source", verify="rerun", order=50):
        self.id, self.category, self.names, self.weak_names, self.phrases = id, category, tuple(names), tuple(weak_names), tuple(phrases)
        self.maintenance, self.accepts, self.companions = maintenance, tuple(accepts), tuple(companions)
        self.authorities, self.fix, self.verify, self.order, self._rx = dict(authorities or {}), fix, verify, order, None

    def _requested_rx(self):
        if self._rx is None:
            alts = []
            if self.names:
                n = r"(?<![\w-])(?:" + "|".join(self.names) + r")(?![\w-])"
                alts += [n + r"(?:\W+[\w-]+){0,4}?\W+" + FINDINGS + r"(?![\w-])",
                         r"(?<![\w-])" + FINDINGS + r"(?:\W+[\w-]+){0,4}?\W+" + n,
                         r"(?<![\w-])" + FIX_VERB + r"(?:\W+[\w-]+){0,3}?\W+" + n]
            if self.weak_names:
                w = r"(?<![\w-])(?:" + "|".join(self.weak_names) + r")(?![\w-])"
                alts += [w + r"(?:\W+[\w-]+){0,2}?\W+" + WEAK_FINDINGS + r"(?![\w-])", r"(?<![\w-])(?:failing|failed|broken|red)\W+" + w]
            if self.phrases:
                alts.append(r"(?<![\w-])(?:" + "|".join(self.phrases) + r")(?![\w-])")
            self._rx = re.compile("|".join(alts), re.I) if alts else re.compile(r"(?!)")
        return self._rx

    def requested(self, task):
        """Does the task depend on this source's findings? Mentioning the tool is not enough ("update eslint config")."""
        return bool(self._requested_rx().search(task or ""))

    def names_token(self, token):
        return any(re.fullmatch(n, token, re.I) for n in self.names + self.weak_names)

    def need_for_tags(self, tags):
        return None

    def detect(self, ctx, explicit):
        return unavailable(f"{self.id}: no detector")

    def parse(self, text, ctx=None, failed=False):
        return parse_text(text, self.id, failed)


class Context:
    """Per-run, lazily computed facts shared by adapters. `probed` records which adapters were asked to detect, so lazy
    discovery is observable (and testable)."""

    def __init__(self, root, rmap=None, task="", evidence=None, state_dir=None):
        self.root, self.rmap, self.task = Path(root), rmap or {}, task
        self.evidence = evidence or {}
        self.state_dir = Path(state_dir) if state_dir else self.root / ".ai" / "state"
        self.probed, self._pkg, self._gen = [], False, None

    @property
    def pkg(self):
        if self._pkg is False:
            self._pkg = package_scripts(self.root, self.rmap)
        return self._pkg

    @property
    def is_gen(self):
        if self._gen is None:
            self._gen = generated_classifier(self.root)
        return self._gen

    def which(self, name):
        return shutil.which(name)

    def state_rel(self, *parts):
        """Repo-relative path under the (git-ignored) state dir, for reports a command writes."""
        return str((self.state_dir.joinpath(*parts)).relative_to(self.root)) if self.state_dir.is_relative_to(self.root) \
            else str(self.state_dir.joinpath(*parts))
