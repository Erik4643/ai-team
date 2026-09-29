"""Evidence routing: task-dependent required sources, the early-exit rule, the adapter registry, supplied evidence, project
custom checks and finding classes. No model calls: providers are stubbed (forbid() raises) and CLIs are faked."""
import contextlib, hashlib, importlib.machinery, importlib.util, io, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

KIT = Path(os.environ.get("AI_KIT", str(Path(__file__).resolve().parents[1])))
loader = importlib.machinery.SourceFileLoader("ai_team_evidence_tests", str(KIT / "bin" / "ai-team"))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)
D, core = m.DIAG, m.DIAG.core
m.record = lambda *a, **k: None
m.metrics = lambda: []
m._HIST = []
m.COOLDOWN_FILE = Path(tempfile.mkdtemp()) / "cooldown.json"
FAKE_WHICH = lambda b: f"/usr/bin/{b}" if b in ("codex", "claude") else None  # noqa: E731
WEBSTORM_TASK = ("Audit WebStorm project inspection results, fix all real source issues, classify generated/vendor/IDE-only noise "
                 "correctly, and verify the project afterward.")


def forbid(*a, **k):
    raise AssertionError("a real provider call was attempted")


def git_repo(files, after=None):
    d = Path(tempfile.mkdtemp(prefix="evidence repo "))
    for rel, body in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body if isinstance(body, str) else json.dumps(body))
    subprocess.run("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", shell=True, cwd=d, check=True)
    for rel, body in (after or {}).items():  # untracked / ignored files created after the commit
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    return d


def jetbrains_export(problems):
    """A JetBrains 'Export to XML' directory: one file per inspection."""
    d = Path(tempfile.mkdtemp(prefix="inspections "))
    by = {}
    for code, sev, file, line, text in problems:
        by.setdefault(code, []).append(f"<problem><file>file://$PROJECT_DIR$/{file}</file><line>{line}</line><module>app</module>"
                                       f"<problem_class id=\"{code}\" severity=\"{sev}\">{code}</problem_class><description>{text}</description></problem>")
    for code, items in by.items():
        (d / f"{code}.xml").write_text("<problems is_local_tool=\"true\">" + "".join(items) + "</problems>")
    (d / ".descriptions.xml").write_text("<inspections/>")
    return d


class Base(unittest.TestCase):
    def setUp(self):
        self.calls, self.marker = [], Path(tempfile.mkdtemp()) / "checks-ran"
        m.run_provider = forbid
        m.BROKEN.clear()
        m.COOLDOWN_FILE.write_text("{}")
        which = patch.object(m.shutil, "which", side_effect=FAKE_WHICH)
        which.start()
        self.addCleanup(which.stop)
        m._AVAIL.clear()

    def frontend(self, commands=None, extra=None):
        """JetBrains-configured project (.idea, qodana.yaml) whose ordinary checks pass and leave a marker when they run."""
        commands = commands or {"typecheck": f"touch {self.marker}", "lint": "true"}
        files = {".gitignore": "dist-*\n", ".idea/inspectionProfiles/Project_Default.xml": "<component/>", "qodana.yaml": "linter: jetbrains/qodana-js\n",
                 ".ai/repo-map.json": {"commands": commands}, "src/a.ts": "export const a = 1\n", "src/b.ts": "export const b = 2\n"}
        files.update(extra or {})
        return git_repo(files, after={"dist-dev/assets/index.css": ".b{-webkit-box:1}\n"})

    def stub(self, edit=lambda prompt: None):
        def run(provider, tier, prompt, write, role, tdir, label, allow, **k):
            self.calls.append((role, prompt))
            edit(prompt)
            return json.dumps({"status": "done", "confidence": 0.9}), {"in": 1, "cached": 0, "out": 1, "usd": None}, "stub"
        m.run_provider = run

    def run_task(self, root, task, evidence=None):
        """plan → execute (or the local evidence audit), stdout captured. → (rc, stdout, plan)"""
        p = m.plan(task, "balanced", None, root, evidence=evidence)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rmap = m.load_map(root)
            rc = m.local(p, root, rmap, task) if p.get("local") else m.Run(task, p, root, rmap).execute()
        return rc, buf.getvalue(), p


class EvidenceRouting(Base):
    def test_requested_sources_are_task_dependent(self):
        for task, want in (("fix Stylelint errors", ["STYLELINT"]), ("fix TypeScript errors", ["TYPECHECK"]),
                           ("Fix all CI validation failures", ["CI_DIAGNOSTICS"]), ("Fix WebStorm inspection errors", ["JETBRAINS_INSPECTION"]),
                           (WEBSTORM_TASK, ["JETBRAINS_INSPECTION"]), ("fix Qodana issues", ["QODANA"]), ("make the tests pass", ["TEST"]),
                           ("the build is broken", ["BUILD"]), ("fix all Sonar issues", ["SONAR"]), ("fix eslint", ["LINT"]),
                           ("update eslint config to allow console", []), ("add a test for the login form", []), ("fix React warnings", []),
                           ("fix the login form error message", []), ("fix all existing errors and warnings in this project", []),
                           ("open the project in WebStorm and rename Foo to Bar", [])):
            with self.subTest(task=task):
                self.assertEqual(D.requested_ids(task), want)

    def test_ordinary_maintenance_finishes_without_jetbrains_evidence(self):
        root = self.frontend()
        rc, out, p = self.run_task(root, "fix all existing errors and warnings in this project")
        self.assertEqual(rc, 0, out)
        self.assertIn("DONE ·", out)
        self.assertNotIn("JETBRAINS_INSPECTION", [r["source"] for r in p["diagnostics"]])
        self.assertTrue(self.marker.exists())                                   # the ordinary checks really ran

    def test_explicit_webstorm_task_cannot_early_exit_on_tsc_eslint(self):
        root = self.frontend()
        rc, out, p = self.run_task(root, WEBSTORM_TASK)
        self.assertEqual(rc, 1)
        self.assertIn("BLOCKED ·", out); self.assertNotIn("DONE ·", out)
        self.assertIn("REQUIRED_EVIDENCE_UNAVAILABLE: JETBRAINS_INSPECTION", out)
        self.assertIn("--evidence JETBRAINS_INSPECTION=", out)                  # precise unblock step
        self.assertIn("Model calls: 0", out)
        self.assertFalse(self.marker.exists())                                  # blocked before any check ran
        rc, out, p = self.run_task(root, "Audit WebStorm project inspection results")  # read-only phrasing: local audit
        self.assertTrue(p["evidence_audit"]); self.assertEqual(rc, 1); self.assertIn("BLOCKED ·", out)

    def test_explicit_stylelint_task_makes_stylelint_required(self):
        root = git_repo({".ai/repo-map.json": {"commands": {"typecheck": "true", "lint": "true"}}, "src/app.css": ".a{}\n"})
        rows = m.plan("fix Stylelint errors", "balanced", None, root)["diagnostics"]
        self.assertEqual([(r["source"], r["need"], r["state"]) for r in rows], [("STYLELINT", "required", "unavailable")])
        rc, out, _ = self.run_task(root, "fix Stylelint errors")
        self.assertEqual(rc, 1); self.assertIn("REQUIRED_EVIDENCE_UNAVAILABLE: STYLELINT", out)
        rows = {r["source"]: r for r in m.plan("fix all existing errors and warnings in this project", "balanced", None, root)["diagnostics"]}
        self.assertEqual((rows["STYLELINT"]["need"], rows["STYLELINT"]["state"]), ("optional", "not configured"))  # not a gap here
        node = git_repo({"package.json": {"scripts": {"lint:css": "stylelint \"**/*.css\" --fix"}}, "src/app.css": ".a{}\n"})
        row = m.plan("fix Stylelint errors", "balanced", None, node)["diagnostics"][0]
        self.assertEqual((row["state"], row["cmd"]), ("run", "npx stylelint '**/*.css'"))

    def test_required_unavailable_blocks_and_optional_unavailable_warns(self):
        root = git_repo({"package.json": {"scripts": {"typecheck": "tsc -b"}}, "src/a.ts": "x\n"})       # tsc -b: no read-only form
        clean = jetbrains_export([])
        rc, out, _ = self.run_task(root, "Audit WebStorm inspection results", {"JETBRAINS_INSPECTION": [str(clean)]})
        self.assertEqual(rc, 0, out)
        self.assertIn("DONE ·", out); self.assertIn("Verify:      PASS_WITH_WARNINGS", out)      # optional companion unavailable
        rc, out, _ = self.run_task(root, "Audit WebStorm inspection results")
        self.assertEqual(rc, 1); self.assertIn("BLOCKED ·", out)                                  # required source unreadable

    def test_zero_provider_calls_for_planning_discovery_and_audits(self):
        root = self.frontend()
        calls = m.MODEL_CALLS[0]
        for task in (WEBSTORM_TASK, "fix Stylelint errors", "Fix all CI validation failures", "fix all Sonar issues",
                     "fix all existing errors and warnings in this project"):
            p = m.plan(task, "balanced", None, root)
            with contextlib.redirect_stdout(io.StringIO()):
                m.print_plan(p, root, {}, task)
        with contextlib.redirect_stdout(io.StringIO()):
            self.run_task(root, "Audit WebStorm inspection results", {"": [str(jetbrains_export([]))]})
        self.assertEqual(m.MODEL_CALLS[0], calls)


class EvidenceSources(Base):
    def test_supplied_clean_report_satisfies_required_evidence(self):
        root = self.frontend()
        sarif = Path(tempfile.mkdtemp()) / "qodana.sarif.json"
        sarif.write_text(json.dumps({"version": "2.1.0", "runs": [{"tool": {"driver": {"name": "QDJS"}}, "results": []}]}))
        rc, out, p = self.run_task(root, WEBSTORM_TASK, {"": [str(sarif)]})   # unlabelled Qodana SARIF → JetBrains (accepts QODANA)
        rows = {r["source"]: r for r in p["diagnostics"]}
        self.assertEqual(rows["JETBRAINS_INSPECTION"]["evidence"], [str(sarif)])
        self.assertEqual(rc, 0, out); self.assertIn("DONE ·", out)

    def test_supplied_findings_are_classified_and_only_real_source_is_fixed(self):
        root = self.frontend()
        export = jetbrains_export([
            ("JSUnusedLocalSymbols", "WARNING", "src/a.ts", 1, "Unused constant <code>a</code>"),
            ("CssUnknownProperty", "WARNING", "dist-dev/assets/index.css", 1, "Unknown CSS property -webkit-box"),
            ("JSUnresolvedLibrary", "WARNING", "node_modules/lib/index.js", 3, "Unresolved library"),
            ("TypeScriptUnresolvedReference", "ERROR", "src/b.ts", 1, "Unresolved variable b"),
            ("SpellCheckingInspection", "TYPO", "src/b.ts", 1, "Typo: In word 'recieve'"),
            ("RedundantLocalVariable", "WEAK WARNING", "src/b.ts", 1, "Redundant local variable"),
            ("XmlUnusedNamespaceDeclaration", "WARNING", ".idea/workspace.xml", 1, "Unused namespace")])

        def edit(prompt):
            (root / "src/a.ts").write_text("export {}\n")
            (root / "dist-dev/assets/index.css").write_text(".b{display:flex}\n")   # agent also "fixes" build output
        self.stub(edit)
        rc, out, _ = self.run_task(root, "fix all WebStorm inspection warnings", {"JETBRAINS_INSPECTION": [str(export)]})
        impl = [p for role, p in self.calls if role == "implementer"]
        self.assertEqual(len(impl), 1, out)
        self.assertIn("- src/a.ts", impl[0])
        for noise in ("- src/b.ts", "- dist-dev", "- node_modules", "- .idea"):
            self.assertNotIn(noise, impl[0])
        for cls in ("GENERATED_OUTPUT", "THIRD_PARTY", "IDE_FALSE_POSITIVE", "SPELLING", "LOW_VALUE_WARNING", "CONFIGURATION_NOISE"):
            self.assertIn(cls, out)
        self.assertEqual((root / "dist-dev/assets/index.css").read_text(), ".b{-webkit-box:1}\n")   # generated output protected
        self.assertEqual(rc, 1)
        self.assertIn("NOT DONE ·", out); self.assertIn("Verify:      PENDING_EVIDENCE", out)       # static evidence: re-read to verify
        self.assertIn("--evidence JETBRAINS_INSPECTION=<new report>", out)

    def test_unknown_source_is_never_fabricated(self):
        root = self.frontend()
        rc, out, p = self.run_task(root, "fix all Sonar issues")
        self.assertEqual(rc, 1)
        self.assertIn("REQUIRED_EVIDENCE_UNAVAILABLE: SONAR", out); self.assertNotIn("DONE ·", out)
        self.assertFalse(self.marker.exists())                                  # tsc/eslint results were not used as a substitute
        report = Path(tempfile.mkdtemp()) / "sonar.json"
        report.write_text(json.dumps({"diagnostics": []}))
        rc, out, _ = self.run_task(root, "fix all Sonar issues", {"SONAR": [str(report)]})
        self.assertEqual(rc, 0, out); self.assertIn("DONE ·", out)

    def test_unreadable_evidence_blocks(self):
        root = self.frontend()
        bad = Path(tempfile.mkdtemp()) / "notes.txt"
        bad.write_text("everything looks fine to me\n")
        rc, out, _ = self.run_task(root, "Audit WebStorm inspection results", {"JETBRAINS_INSPECTION": [str(bad)]})
        self.assertEqual(rc, 1); self.assertIn("supplied evidence unreadable", out)

    def test_ci_sources(self):
        deploy_only = git_repo({".gitlab-ci.yml": "build:\n  script:\n    - docker build --push -t x .\ndeploy:\n  script:\n  - git push\n",
                                ".ai/repo-map.json": {"commands": {"typecheck": "true"}}})
        rc, out, _ = self.run_task(deploy_only, "Fix all CI validation failures")
        self.assertEqual(rc, 1); self.assertIn("REQUIRED_EVIDENCE_UNAVAILABLE: CI_DIAGNOSTICS", out)
        self.assertIn("remote pipeline results are not readable", out); self.assertIn("--evidence CI_DIAGNOSTICS=", out)
        gh = git_repo({"package.json": {"scripts": {"lint": "eslint . --fix", "deploy": "vercel --prod"}},
                       ".github/workflows/ci.yml": "jobs:\n  ci:\n    steps:\n      - run: npm ci\n      - run: npm run lint\n      - run: |\n          npm run deploy\n"})
        row = m.plan("Fix all CI validation failures", "balanced", None, gh)["diagnostics"][0]
        self.assertEqual((row["source"], row["state"], row["cmds"]), ("CI_DIAGNOSTICS", "run", ["npx eslint ."]))  # deploy/install never run
        log = Path(tempfile.mkdtemp()) / "job.log"
        log.write_text("src/a.ts(1,7): error TS2322: Type 'string' is not assignable to type 'number'.\n")
        rows = m.plan("Fix all CI validation failures", "balanced", None, deploy_only, evidence={"CI": [str(log)]})["diagnostics"]
        self.assertEqual([(r["source"], r["state"]) for r in rows if r["need"] == "required"], [("CI_DIAGNOSTICS", "run")])

    def test_qodana_cli_is_machine_readable_jetbrains_evidence(self):
        fakebin = Path(tempfile.mkdtemp())
        (fakebin / "qodana").write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do [ "$1" = "--results-dir" ] && out="$2"; shift; done\n'
                                        'mkdir -p "$out"\nprintf \'%s\' \'{"runs":[{"tool":{"driver":{"name":"QDJS"}},"results":[{"ruleId":"JSUnusedLocalSymbols",'
                                        '"level":"warning","message":{"text":"Unused constant a"},"locations":[{"physicalLocation":{"artifactLocation":'
                                        '{"uri":"src/a.ts"},"region":{"startLine":1}}}]}]}]}\' > "$out/qodana.sarif.json"\n')
        (fakebin / "qodana").chmod(0o755)
        root = self.frontend()
        which = lambda b: str(fakebin / "qodana") if b == "qodana" else FAKE_WHICH(b)  # noqa: E731
        with patch.object(m.shutil, "which", side_effect=which), patch.dict(os.environ, {"PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}"}):
            rc, out, p = self.run_task(root, "Audit WebStorm inspection results")
        row = next(r for r in p["diagnostics"] if r["source"] == "JETBRAINS_INSPECTION")
        self.assertTrue(row["cmd"].startswith("qodana scan --results-dir")); self.assertEqual(row["parser"], "sarif")
        self.assertEqual(rc, 1, out); self.assertIn("FINDINGS ·", out); self.assertIn("JSUnusedLocalSymbols", out)


class EvidenceRegistry(Base):
    SONAR = ('from .core import Adapter, runnable, unavailable\n\n\nclass Sonar(Adapter):\n    def detect(self, ctx, explicit):\n'
             '        return (runnable("cat sonar-report.json", parser="json", confidence="high", note="sonar-report.json")\n'
             '                if (ctx.root / "sonar-report.json").is_file() else unavailable("no sonar-report.json"))\n\n\n'
             'ADAPTERS = [Sonar("SONAR", "sonar", names=(r"sonar\\w*",), order=85)]\n')

    def test_adapter_discovery_is_lazy(self):
        root = self.frontend()
        ctx = D.Context(root, m.load_map(root), "fix all existing errors and warnings in this project")
        D.plan_rows(ctx.task, ctx, maintenance=True, broad=True)
        self.assertEqual(ctx.probed, ["TYPECHECK", "LINT", "STYLELINT", "TEST", "FORMAT", "BUILD"])   # no IDE / Qodana / CI probing
        ctx = D.Context(root, {}, "fix Stylelint errors")
        D.plan_rows(ctx.task, ctx)
        self.assertEqual(ctx.probed, ["STYLELINT"])
        ctx = D.Context(root, {}, "add a loading state to the submit button")
        self.assertEqual((D.plan_rows(ctx.task, ctx), ctx.probed), ([], []))
        with patch.object(type(D.adapters()["JETBRAINS_INSPECTION"]), "detect", side_effect=AssertionError("probed")):
            m.plan("fix all existing errors and warnings in this project", "balanced", None, root)

    def test_new_adapter_module_needs_no_orchestrator_change(self):
        kit = Path(tempfile.mkdtemp(prefix="kit ")) / "ai-kit"
        shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns("__pycache__", ".git", "state", ".idea"))
        engine = hashlib.sha256((kit / "bin/ai-team").read_bytes()).hexdigest()
        root = git_repo({".ai/repo-map.json": {"commands": {}}, "src/a.ts": "x\n",
                         "sonar-report.json": {"diagnostics": [{"file": "src/a.ts", "line": 1, "severity": "error", "code": "S1481", "message": "Remove unused"}]}})
        env = {**os.environ, "AI_KIT": str(kit), "PATH": "/usr/bin:/bin"}
        ask = lambda *a: subprocess.run([sys.executable, str(kit / "bin/ai-team"), *a], cwd=root, env=env, capture_output=True, text=True)  # noqa: E731
        plan = json.loads(ask("--plan", "--json", "fix all Sonar issues").stdout)
        self.assertEqual([(r["source"], r["state"]) for r in plan["diagnostics"] if r["source"] == "SONAR"], [("SONAR", "unavailable")])
        (kit / "diagnostics/sonar.py").write_text(self.SONAR)                    # the whole extension: one module
        plan = json.loads(ask("--plan", "--json", "fix all Sonar issues").stdout)
        self.assertEqual([(r["source"], r["state"]) for r in plan["diagnostics"] if r["source"] == "SONAR"], [("SONAR", "run")])
        audit = ask("what are the Sonar issues?")
        self.assertIn("FINDINGS ·", audit.stdout); self.assertIn("S1481", audit.stdout); self.assertIn("Model calls: 0", audit.stdout)
        self.assertEqual(hashlib.sha256((kit / "bin/ai-team").read_bytes()).hexdigest(), engine)

    def test_runtime_registration(self):
        class Semgrep(D.Adapter):
            def detect(self, ctx, explicit):
                return D.unavailable("semgrep is not installed", unblock="--evidence SEMGREP=<semgrep.sarif>")
        D.register(Semgrep("SEMGREP", "semgrep", names=(r"semgrep",), order=86))
        self.addCleanup(D.unregister, "SEMGREP")
        self.assertEqual(D.requested_ids("fix Semgrep findings"), ["SEMGREP"])
        rows = m.plan("fix Semgrep findings", "balanced", None, self.frontend())["diagnostics"]
        self.assertEqual([(r["source"], r["state"], r["unblock"]) for r in rows], [("SEMGREP", "unavailable", "--evidence SEMGREP=<semgrep.sarif>")])


class CustomChecks(Base):
    CHECK = {"id": "api-schema", "command": "python3 tools/check_api.py", "read_only": True, "required_for": ["api", "maintenance"],
             "aliases": ["schema validation"]}
    SCRIPT = ("import pathlib, sys\nbad = 'TODO' in pathlib.Path('src/api.ts').read_text()\n"
              "print('src/api.ts:3:1: error Missing field id' if bad else 'schema ok')\nsys.exit(1 if bad else 0)\n")

    def test_validation_rejects_unsafe_definitions(self):
        ctx = D.Context(git_repo({"a.txt": "x\n"}), {})
        for spec, why in (({**self.CHECK, "command": "yarn lint --fix"}, "not read-only"), ({**self.CHECK, "command": "eslint . && rm -rf x"}, "one simple command"),
                          ({**self.CHECK, "command": "eslint $(ls)"}, "one simple command"), ({**self.CHECK, "read_only": "yes"}, "read_only must be true"),
                          ({**self.CHECK, "id": "Bad Id"}, "slug"), ({**self.CHECK, "output": "../outside.json"}, "inside the repository"),
                          ({**self.CHECK, "parser": "xlsx"}, "parser"), ({**self.CHECK, "required_for": "api"}, "list")):
            with self.subTest(why=why):
                check, reason = D.custom.validate(spec, ctx)
                self.assertIsNone(check); self.assertIn(why, reason)
        check, reason = D.custom.validate(self.CHECK, ctx)
        self.assertEqual((check.id, reason), ("CUSTOM:api-schema", ""))

    def test_non_jetbrains_custom_check_runs_by_name_tag_and_maintenance(self):
        root = git_repo({".ai/repo-map.json": {"commands": {"typecheck": "true"}, "custom_checks": [self.CHECK]},
                         "tools/check_api.py": self.SCRIPT, "src/api.ts": "// TODO id\n"})
        rc, out, p = self.run_task(root, "check the api-schema results")               # named → local audit, 0 model calls
        self.assertEqual((rc, p["evidence_audit"]), (1, True), out)
        self.assertIn("FINDINGS ·", out); self.assertIn("Missing field id", out)
        (root / "src/api.ts").write_text("// id: string\n")
        rc, out, _ = self.run_task(root, "check the api-schema results")
        self.assertEqual(rc, 0, out); self.assertIn("DONE ·", out)
        gate = m.plan("add an api endpoint for users", "balanced", None, root)["evidence_gate"]   # tag "api" → required before DONE
        self.assertEqual([(r["source"], r["need"]) for r in gate], [("CUSTOM:api-schema", "required")])
        rows = {r["source"]: r["need"] for r in m.plan("fix all existing errors and warnings in this project", "balanced", None, root)["diagnostics"]}
        self.assertEqual(rows["CUSTOM:api-schema"], "required")                            # required_for: maintenance
        self.assertNotIn("CUSTOM:api-schema", m.plan("fix the login form error message", "balanced", None, root).get("evidence_gate") or [])

    def test_project_can_provide_jetbrains_evidence(self):
        export = "<problems><problem><file>file://$PROJECT_DIR$/src/a.ts</file><line>1</line><problem_class id=\"JSUnusedLocalSymbols\" severity=\"WARNING\">" \
                 "x</problem_class><description>Unused constant a</description></problem></problems>"
        script = f"import pathlib\np = pathlib.Path('.ai/state/ide/Unused.xml'); p.parent.mkdir(parents=True, exist_ok=True); p.write_text({export!r})\n"
        check = {"id": "ide", "command": "python3 tools/ide.py", "read_only": True, "source": "JETBRAINS_INSPECTION", "parser": "jetbrains",
                 "output": ".ai/state/ide/Unused.xml"}
        root = git_repo({".ai/repo-map.json": {"commands": {}, "custom_checks": [check]}, "tools/ide.py": script, "src/a.ts": "export const a = 1\n"})
        rc, out, p = self.run_task(root, "Audit WebStorm inspection results")
        row = next(r for r in p["diagnostics"] if r["source"] == "JETBRAINS_INSPECTION")
        self.assertEqual((row["state"], row["cmd"]), ("run", "python3 tools/ide.py"))
        self.assertEqual(rc, 1, out); self.assertIn("FINDINGS ·", out); self.assertIn("JSUnusedLocalSymbols", out)


class Classification(Base):
    def test_every_format_normalizes_to_one_schema(self):
        d = Path(tempfile.mkdtemp())
        (d / "r.sarif").write_text(json.dumps({"runs": [{"tool": {"driver": {"name": "ESLint"}}, "results": [
            {"ruleId": "no-unused-vars", "level": "error", "message": {"text": "x unused"},
             "locations": [{"physicalLocation": {"artifactLocation": {"uri": "file:///repo/src/a.ts"}, "region": {"startLine": 4, "startColumn": 2}}}]},
            {"ruleId": "old", "level": "error", "message": {"text": "suppressed"}, "suppressions": [{"kind": "inSource"}]}]}]}))
        (d / "junit.xml").write_text('<testsuites><testsuite><testcase classname="cart" name="adds" file="src/cart.test.ts"><failure message="expected 2"/></testcase>'
                                     '<testcase classname="cart" name="ok"/></testsuite></testsuites>')
        (d / "f.json").write_text(json.dumps([{"path": "src/x.py", "line": 7, "severity": "warning", "rule": "W1", "message": "m"}]))
        (d / "t.log").write_text("src/a.ts(3,5): error TS2304: Cannot find name 'foo'.\n")
        cases = {"r.sarif": ("LINT", "sarif", 1, ("error", "/repo/src/a.ts", 4, 2, "no-unused-vars")),
                 "junit.xml": ("", "junit", 1, ("error", "src/cart.test.ts", None, None, "failure")),
                 "f.json": ("", "json", 1, ("warning", "src/x.py", 7, None, "W1")),
                 "t.log": ("", "text", 1, ("error", "src/a.ts", 3, 5, "TS2304"))}
        for name, (src, fmt, n, first) in cases.items():
            with self.subTest(name=name):
                got_src, found, got_fmt = core.load_evidence(d / name)
                self.assertEqual((got_src, got_fmt, len(found)), (src, fmt, n))
                f = found[0]
                self.assertEqual((f["severity"], f["file"], f["line"], f["column"], f["code"]), first)
                self.assertTrue({"source", "message", "fingerprint"} <= set(f))

    def test_classes_are_deterministic(self):
        is_gen = lambda p: p.startswith("dist-dev/")  # noqa: E731
        c = lambda sev, file, code="", scope=(), dup=(): core.classify(core.finding("X", sev, file, 1, None, code, "m"), is_gen, None, scope, dup)["class"]  # noqa: E731
        self.assertEqual(c("error", "src/a.ts"), "REAL_SOURCE_ERROR")
        self.assertEqual(c("warning", "src/a.ts"), "REAL_SOURCE_WARNING")
        self.assertEqual(c("warning", "node_modules/x/i.js"), "THIRD_PARTY")
        self.assertEqual(c("warning", "dist-dev/app.css"), "GENERATED_OUTPUT")
        self.assertEqual(c("warning", ".idea/workspace.xml"), "CONFIGURATION_NOISE")
        self.assertEqual(c("typo", "src/a.ts"), "SPELLING")
        self.assertEqual(c("error", "src/a.ts", "TypeScriptUnresolvedReference", dup=("TypeScriptUnresolvedReference",)), "IDE_FALSE_POSITIVE")
        self.assertEqual(c("weak warning", "src/a.ts"), "LOW_VALUE_WARNING")
        self.assertEqual(c("error", "src/other.ts", scope=("src/a.ts",)), "OUT_OF_SCOPE")

    def test_unsafe_or_empty_evidence_is_rejected(self):
        d = Path(tempfile.mkdtemp())
        (d / "evil.xml").write_text('<!DOCTYPE x [<!ENTITY a "aaaa">]><problems>&a;</problems>')
        (d / "empty.txt").write_text("")
        for name, why in (("evil.xml", "DTD"), ("empty.txt", "empty"), ("missing.json", "not found")):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, why):
                core.load_evidence(d / name)


if __name__ == "__main__":
    unittest.main()
