"""ai-team routing / context / security regression suite. No model calls: CLIs are faked, providers are stubbed.

Run: ai-team --self-test      (or: python3 -m unittest discover -s ~/.ai-kit/tests)
"""
import contextlib, importlib.machinery, importlib.util, io, json, os, subprocess, tempfile, unittest
from pathlib import Path

SRC = Path(os.environ.get("AI_KIT", str(Path(__file__).resolve().parents[1]))) / "bin" / "ai-team"
loader = importlib.machinery.SourceFileLoader("ai_team_under_test", str(SRC))
spec = importlib.util.spec_from_loader("ai_team_under_test", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

# Isolation: fake installed CLIs (codex + claude, no gemini), no history, no real cooldowns, no metric writes, no real calls.
m.shutil.which = lambda b: f"/usr/bin/{b}" if b in ("codex", "claude") else None
m.metrics = lambda: []
m._HIST = []
m.record = lambda *a, **k: None
m.COOLDOWN_FILE = Path(tempfile.mkdtemp()) / "cooldown.json"
m._AVAIL.clear()


def forbid(*a, **k):
    raise AssertionError("a real provider call was attempted")


REAL_RUN_PROVIDER = m.run_provider
REAL_VERIFY = m.verify
REAL_DIAGNOSE = m.diagnose
m.run_provider = forbid


def git_repo(files):
    d = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    subprocess.run("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", shell=True, cwd=d, check=True)
    return d


CMDS = {"typecheck": "tc", "lint": "lint", "test": "test", "build": "build"}
NO_AUTH = git_repo({"src/cart.js": "module.exports = 1\n", "README.md": "# x\n",
                    "package.json": '{"scripts":{"typecheck":"true","lint":"true","test":"true","build":"true"}}'})
WITH_AUTH = git_repo({"src/auth/session.ts": "export const session = {}\n"})


def plan(task, budget="balanced", root=NO_AUTH, forced=None):
    return m.plan(task, budget, forced, root)


def roles(p, stage=None):
    return [s["role"] for s in p["steps"] if stage is None or s["stage"] == stage]


def stub(responses):
    """run_provider stub: responses[role] is a dict or callable(tier) → dict. Records (role, provider, tier)."""
    calls = []

    def run(provider, tier, prompt, write, role, tdir, label, allow, **k):
        calls.append((role, provider, tier))
        r = responses.get(role, {"status": "done", "confidence": 0.9})
        r = r(tier) if callable(r) else r
        return json.dumps(r), {"in": 1, "cached": 0, "out": 1, "usd": None}, "stub"
    m.run_provider = run
    return calls


class Base(unittest.TestCase):
    def setUp(self):
        m._HIST = []
        m.BROKEN.clear()
        m._AVAIL.clear()
        m.run_provider = forbid
        m.verify = lambda *a: (True, [])
        m.diagnose = REAL_DIAGNOSE
        m.COOLDOWN_FILE.write_text("{}")
        os.environ.pop("AI_TEAM_PROVIDERS", None)
        subprocess.run("git checkout -q . && git clean -qfd -e .ai", shell=True, cwd=NO_AUTH)


EVAL = [  # (id, task, intent, complexity or None, required roles, extra assertion)
    (1, "ты работаешь?", "META", "TRIVIAL", [], "local"),
    (2, "show providers", "META", "TRIVIAL", [], "local"),
    (3, "explain what this component does", "QUESTION", "SMALL", ["explorer"], "readonly"),
    (4, "where is authentication configured?", "QUESTION", "SMALL", ["explorer"], "readonly"),
    (5, "change the border radius of the login button", "IMPLEMENTATION", "MICRO", ["implementer"], "micro"),
    (6, "fix typo in README", "IMPLEMENTATION", "MICRO", ["implementer"], "micro-docs"),
    (7, "add loading state to submit button", "IMPLEMENTATION", "SMALL", ["implementer"], "single"),
    (8, "find why this component renders twice", "DEBUG", None, ["explorer"], "cheap-debug"),
    (9, "find and fix an authentication race condition", "DEBUG", "COMPLEX", ["explorer", "implementer"], "dag"),
    (10, "redesign authentication architecture", "ARCHITECTURE", "COMPLEX", ["explorer", "architect"], "architecture"),
    (11, "review my current changes", "REVIEW", None, [], "local"),   # clean tree → nothing to review (sized cases: ReviewSizing)
    (12, "check whether dependency X changed its API in the latest version", "RESEARCH", None, ["researcher"], "readonly"),
    (13, "fix all current TypeScript errors", "DEBUG", "MEDIUM", ["implementer"], "maintenance"),
    (14, "tell me what build command this project uses", "META", "TRIVIAL", [], "local"),
    (15, "plan how to add pagination to the list page", "PLAN", None, ["explorer"], "plan"),
    (16, "how should we implement pagination?", "PLAN", None, ["explorer"], "plan"),
    (17, "clean up the codebase", "IMPLEMENTATION", "MEDIUM", ["implementer"], "maintenance"),
    (18, "clean up the project?", "QUESTION", None, ["explorer"], "readonly"),
]


class Eval(Base):
    def test_scenarios(self):
        for i, task, intent, cx, req, kind in EVAL:
            with self.subTest(i=i, task=task):
                p = plan(task)
                self.assertEqual(p["intent"], intent)
                if cx:
                    self.assertEqual(p["complexity"], cx)
                self.assertEqual(roles(p, "required"), req)
                self.assertFalse(any(s["tier"] == 3 for s in p["steps"]), "T3 at plan time needs an escalation reason")
                self.assertFalse(any(s["provider"] == "gemini" for s in p["steps"]), "unavailable provider selected")
                if kind == "local":
                    self.assertTrue(p.get("local")); self.assertEqual(p["steps"], [])
                if kind == "readonly":
                    self.assertNotIn("implementer", roles(p)); self.assertEqual(max(s["tier"] for s in p["steps"]), 1)
                if kind.startswith("micro"):
                    self.assertEqual(roles(p), ["implementer"]); self.assertEqual(p["steps"][0]["tier"], 1)
                if kind == "micro-docs":
                    self.assertEqual(m.select_checks(["README.md"], "MICRO", task, CMDS)[0], [])
                if kind == "single":
                    self.assertEqual(roles(p), ["implementer"])
                if kind == "cheap-debug":
                    self.assertNotIn("architect", roles(p, "required")); self.assertEqual(p["steps"][0]["tier"], 1)
                if kind == "dag":
                    self.assertEqual(roles(p, "conditional"), ["architect", "reviewer"])
                if kind == "architecture":
                    self.assertEqual(roles(p, "approval"), ["implementer"])
                if kind == "review":
                    self.assertNotIn("implementer", roles(p))
                if kind == "maintenance":
                    self.assertTrue(p["signals"]["maintenance"]); self.assertNotIn("explorer", roles(p))
                if kind in ("plan", "readonly"):
                    self.assertNotIn("implementer", roles(p)); self.assertFalse(p["signals"].get("aggregate"))

    def test_conditional_stages_never_in_minimum(self):
        p = plan("find and fix an authentication race condition")
        req = sum(s["est"] for s in p["steps"] if s["stage"] == "required")
        exp = sum(s["est"] for s in p["steps"] if s["stage"] in ("required", "conditional"))
        self.assertLess(req, exp)

    def test_critical_needs_repo_evidence(self):
        self.assertEqual(plan("fix authentication race condition")["complexity"], "COMPLEX")
        p = plan("fix authentication race condition", root=WITH_AUTH)
        self.assertEqual(p["complexity"], "CRITICAL")
        self.assertEqual(roles(p, "required"), ["explorer", "architect", "implementer"])
        self.assertFalse(any(s["tier"] == 3 for s in p["steps"]))


class ZeroToken(Base):
    def test_local_paths_make_no_calls(self):
        for t in ("ты работаешь?", "show providers", "какая модель будет использована?", "tell me what build command this project uses",
                  "git status", "что ты будешь делать?"):
            with self.subTest(t=t):
                p = plan(t)
                self.assertTrue(p.get("local"))
                m.local(p, NO_AUTH, m.load_map(NO_AUTH), t)  # forbid() raises if a provider is touched

    def test_plan_and_context_plan_make_no_calls(self):
        for t in ("find and fix an authentication race condition", "explain what this component does"):
            p = plan(t)
            m.print_plan(p, NO_AUTH, {}, t)
            m.print_context_plan(p, NO_AUTH, {}, t)

    def test_clean_diagnostics_need_no_model(self):
        # Shell-only checks: no package manager is required by the deterministic suite.
        root = git_repo({"src/file.py": "x = 1\n"})
        self.assertEqual(m.Run("fix all errors", plan("fix all errors", root=root), root,
                               {"commands": {k: "true" for k in CMDS}}).execute(), 0)


class ContextBudget(Base):
    LIMITS = {"QUESTION": 1000, "MICRO": 600, "SMALL": 800, "MEDIUM": 1500, "COMPLEX": 1500}

    def test_orchestrator_context_regression(self):
        for i, task, intent, cx, req, kind in EVAL:
            p = plan(task)
            if p.get("local"):
                continue
            limit = self.LIMITS.get(intent if intent == "QUESTION" else p["complexity"], 1500)
            for s in p["steps"]:
                with self.subTest(i=i, role=s["role"]):
                    self.assertLessEqual(s["ctx"], limit, f"{task}: {s['role']} context {s['ctx']} > {limit}")

    def test_progressive_disclosure(self):
        root = git_repo({".ai/CONTEXT.md": "# P\n\n## Project facts\n" + "fact\n" * 50 + "\n## Graphify\n" + "g\n" * 50 + "\n## Rules\n- r\n"})
        _, micro = m.build_prompt("implementer", "TASK: x\nNEXT ACTION: y", root, {}, "MICRO")
        _, med = m.build_prompt("implementer", "TASK: x\nNEXT ACTION: y", root, {}, "MEDIUM")
        _, exp = m.build_prompt("explorer", "TASK: x\nNEXT ACTION: y", root, {"dirs": ["a"]}, "SMALL")
        self.assertLess(micro["project"], med["project"])   # MICRO editor gets Rules only
        self.assertEqual(exp["project"], med["project"])  # no valid graph/CLI: graph instructions stay unloaded
        self.assertNotIn("repo-map", micro)
        self.assertIn("repo-map", exp)

    def test_oversized_context_is_trimmed(self):
        _, sizes = m.build_prompt("reviewer", "TASK: x\nNEXT ACTION: y", NO_AUTH, {}, "SMALL", extra="x" * 40000)
        self.assertTrue(sizes.get("_trimmed"))
        self.assertLessEqual(sum(v for k, v in sizes.items() if not k.startswith("_")), m.CFG["context_budget"]["SMALL"] + 60)

    def test_answer_mode_carries_only_the_task(self):
        _, sizes = m.build_prompt("answer", "what is a race condition?", NO_AUTH, {}, "SMALL")
        self.assertEqual(list(sizes), ["task"])

    def test_permanent_kit_files_stay_small(self):
        for f in (m.KIT / "roles").glob("*.md"):
            with self.subTest(f=f.name):
                self.assertLessEqual(len(f.read_text()), 600 if f.stem == "orchestrator" else 400)
        self.assertLessEqual(len((m.KIT / "template" / "CONTEXT.md").read_text()), 1200)
        for s in m.SCHEMA.values():
            self.assertLessEqual(len(s), 600)

    def test_no_volatile_data_in_stable_prefix(self):
        p1, _ = m.build_prompt("implementer", "TASK: a\nNEXT ACTION: b", NO_AUTH, {}, "SMALL")
        p2, _ = m.build_prompt("implementer", "TASK: c\nNEXT ACTION: d", NO_AUTH, {}, "SMALL")
        prefix = p1.split("# Handoff")[0]
        self.assertEqual(prefix, p2.split("# Handoff")[0])
        self.assertNotRegex(prefix, r"20\d\d-\d\d-\d\d|T20\d{6}")


class Fallback(Base):
    def test_single_provider(self):
        os.environ["AI_TEAM_PROVIDERS"] = "claude"
        self.assertEqual({s["provider"] for s in plan("find and fix an authentication race condition")["steps"]}, {"claude"})

    def test_no_provider_is_never_selected(self):
        os.environ["AI_TEAM_PROVIDERS"] = "none"
        p = plan("add loading state to submit button")
        self.assertTrue(all(s["provider"] is None and s["stage"] == "skipped" for s in p["steps"]))

    def test_cooldown_excludes_provider(self):
        m.set_cooldown("codex", 5)
        self.assertNotIn("codex", m.available())
        m.set_cooldown("codex", -1)
        self.assertIn("codex", m.available())

    def test_runtime_failure_falls_back(self):
        calls = []

        def run(provider, tier, prompt, write, role, *a, **k):
            calls.append(provider)
            if provider == "codex":
                raise m.ProviderError("model not found")
            return json.dumps({"status": "no_change"}), {"in": 1, "cached": 0, "out": 1, "usd": None}, "stub"
        m.run_provider = run
        p = plan("add loading state to submit button")
        self.assertEqual(p["steps"][0]["provider"], "codex")
        m.Run("t", p, NO_AUTH, {"commands": {}}).execute()
        self.assertEqual(calls, ["codex", "claude"])


class CostAware(Base):
    def row(self, role, ok, provider="claude", n=30):
        return [{"provider": provider, "role": role, "tier": 1, "in": 2500, "cached": 20000, "out": 500, "ctx": 300, "s": 10, "ok": ok}] * n

    def test_read_only_prefers_cheapest_capable(self):
        self.assertEqual(plan("explain what this component does")["steps"][0]["provider"], "claude")

    def test_code_edit_prefers_editing_capability(self):
        p = plan("add loading state to submit button")
        self.assertEqual(p["steps"][0]["provider"], "codex")
        self.assertIn("capability over cost", p["steps"][0]["why"])

    def test_docs_edit_uses_cheapest_capable_editor(self):
        self.assertEqual(plan("fix typo in README")["steps"][0]["provider"], "claude")

    def test_small_history_does_not_dominate(self):
        m._HIST = self.row("implementer", True, n=2)
        self.assertEqual(plan("add loading state to submit button")["steps"][0]["provider"], "codex")

    def test_strong_history_changes_choice(self):
        m._HIST = self.row("implementer", True)
        self.assertEqual(plan("add loading state to submit button")["steps"][0]["provider"], "claude")

    def test_failures_raise_effective_cost(self):
        m._HIST = self.row("explorer", False)
        self.assertEqual(plan("explain what this component does")["steps"][0]["provider"], "codex")


class EvidenceGuard(Base):
    def row(self, ok, n):
        return [{"provider": "claude", "role": "explorer", "tier": 1, "in": 3000, "cached": 20000, "out": 600, "ctx": 700, "s": 10, "ok": ok}] * n

    def test_weak_history_cannot_overpower_priors(self):
        m._HIST = self.row(False, 4)     # n < 5: weak
        self.assertEqual(plan("explain what this component does")["steps"][0]["provider"], "claude")

    def test_strong_history_can(self):
        m._HIST = self.row(False, 30)    # n ≥ 20: strong
        self.assertEqual(plan("explain what this component does")["steps"][0]["provider"], "codex")

    def test_weight_grows_with_samples(self):
        w = [m.evidence_n(n) / (m.evidence_n(n) + m.CFG["priors"]["success_samples"]) for n in (2, 4, 5, 19, 20, 50)]
        self.assertLess(w[1], 0.25)                        # weak
        self.assertTrue(0.3 < w[2] < w[3] < w[4] < w[5])   # moderate → stronger
        self.assertEqual([m.evidence_label(n) for n in (4, 5, 19, 20)], ["weak", "moderate", "moderate", "strong"])


class ReviewerWording(Base):
    def reviewer(self, task, budget="balanced"):
        return next(s for s in plan(task, budget)["steps"] if s["role"] == "reviewer")

    def test_cross_provider(self):
        r = self.reviewer("find and fix an authentication race condition")
        self.assertIn("cross-provider review", r["why"])
        self.assertNotIn("independent of", r["why"])

    def test_same_provider_is_an_independent_session(self):
        os.environ["AI_TEAM_PROVIDERS"] = "claude"
        r = self.reviewer("find and fix an authentication race condition")
        self.assertIn("independent review session", r["why"])
        self.assertNotIn("independent of", r["why"])


class ReviewSizing(Base):
    BASE = {"src/styles/button.css": ".btn { color: red; }\n", "README.md": "# Readme\n", "src/auth/token.ts": "export const ok = (t) => t.exp > now;\n",
            "package.json": "{}\n"}

    def review(self, edits, budget="balanced"):
        d = git_repo(self.BASE)
        for rel, body in edits.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(body)
        return m.plan("review my current changes", budget, None, d)

    def reviewer(self, p):
        return next(s for s in p["steps"] if s["role"] == "reviewer")

    def test_one_line_css_is_t1(self):
        p = self.review({"src/styles/button.css": ".btn { color: blue; }\n"})
        self.assertEqual((p["review_class"], self.reviewer(p)["tier"]), ("MICRO", 1))
        self.assertEqual(self.reviewer(p)["model"], "haiku")

    def test_ten_line_css_focus_state_is_t1(self):
        focus = ".btn { color: red; }\n" + "".join(f".btn:focus-visible {{ outline: {i}px solid; }}\n" for i in range(9))
        self.assertEqual(self.reviewer(self.review({"src/styles/button.css": focus}))["tier"], 1)

    def test_readme_only_is_t0_or_cheapest(self):
        p = self.review({"README.md": "# Read me\n"})
        self.assertTrue(p.get("local")); self.assertEqual(p["steps"], [])
        q = self.review({"README.md": "# Read me\n"}, "quality")
        self.assertEqual(self.reviewer(q)["tier"], 1)

    def test_one_line_auth_change_is_t2(self):
        p = self.review({"src/auth/token.ts": "export const ok = (t) => t.exp >= now;\n"})
        self.assertEqual((p["review_class"], self.reviewer(p)["tier"]), ("HIGH", 2))

    def test_large_multi_module_diff_is_t2(self):
        big = {f"{mod}/f{i}.ts": "".join(f"export const v{j} = {j};\n" for j in range(30)) for mod in ("app", "lib", "services") for i in range(2)}
        p = self.review(big)
        self.assertEqual(self.reviewer(p)["tier"], 2)
        self.assertIn(p["review_class"], ("MEDIUM", "HIGH"))

    def test_logic_change_is_not_micro(self):
        p = self.review({"src/util.ts": "export function f(a) {\n  if (a) return 1;\n  else if (!a && b) throw new Error();\n}\n"})
        self.assertEqual(self.reviewer(p)["tier"], 2)

    def test_config_change_is_high(self):
        self.assertEqual(self.review({"package.json": '{"name": "x"}\n'})["review_class"], "HIGH")


class BaselineVerification(Base):
    A = "src/a.ts(3,5): error TS2304: Cannot find name 'foo'."
    B = "src/b.ts(10,1): error TS2322: Type 'string' is not assignable to type 'number'."
    C = "src/c.ts(1,1): error TS1005: ';' expected."

    def cmp(self, base, after):
        return m.compare_checks(not base, "\n".join(base), not after, "\n".join(after))

    def test_1_pass(self):
        self.assertEqual(self.cmp([], [])["status"], "PASS")

    def test_2_same_failures(self):
        r = self.cmp([self.A, self.B], [self.A, self.B])
        self.assertEqual((r["status"], r["pre"], r["new"]), ("PASS_WITH_BASELINE_FAILURES", 2, 0))

    def test_3_new_failure(self):
        r = self.cmp([self.A, self.B], [self.A, self.B, self.C])
        self.assertEqual((r["status"], r["new"]), ("FAIL_NEW_ERRORS", 1))
        self.assertIn("src/c.ts", r["new_lines"][0])

    def test_4_improvement(self):
        r = self.cmp([self.A, self.B, self.C], [self.A, self.B])
        self.assertEqual((r["status"], r["fixed"]), ("PASS_WITH_BASELINE_FAILURES", 1))
        self.assertIn("improved: 1 fixed", m.describe_check(dict(r, cmd="tc", ok=True)))

    def test_5_reordered_and_unstable_details_are_unchanged(self):
        shifted_a = "src/a.ts(7,9): error TS2304: Cannot find name 'foo'."   # line moved by an unrelated edit
        noisy = ["\x1b[31m" + self.B + "\x1b[0m", shifted_a, "Done in 3.42s", "12:01:33 Found 2 errors"]
        self.assertEqual(self.cmp([self.A, self.B, "Done in 1.10s"], noisy)["status"], "PASS_WITH_BASELINE_FAILURES")

    def test_regression_from_clean_baseline(self):
        self.assertEqual(self.cmp([], [self.C])["status"], "FAIL_REGRESSION")

    def run_task(self, check, edit):
        repo = git_repo({"src/cart.js": "module.exports = 1\n", "src/other.ts": "x\n", "src/dirty.js": "orig\n"})
        (repo / "src/dirty.js").write_text("user's own uncommitted work\n")  # dirty tree before the task
        def impl(tier):
            (repo / "src/cart.js").write_text(edit)
            return {"status": "done", "confidence": 0.9}
        calls = stub({"implementer": impl})
        m.verify = REAL_VERIFY
        r = m.Run("add loading state to submit button", plan("add loading state to submit button", root=repo), repo, {"commands": {"typecheck": check}})
        rc = r.execute()
        return rc, calls, r, repo

    def test_6_preexisting_unrelated_errors_do_not_retry(self):
        check = "printf 'src/other.ts(3,1): error TS2304: Cannot find name y.\\n'; exit 1"
        rc, calls, r, repo = self.run_task(check, "module.exports = 2\n")
        self.assertEqual(rc, 0)
        self.assertEqual([c[0] for c in calls], ["implementer"])          # no retry
        self.assertEqual(r.escalations, [])                              # no escalation
        self.assertEqual((repo / "src/cart.js").read_text(), "module.exports = 2\n")   # task change restored after baseline
        self.assertEqual((repo / "src/dirty.js").read_text(), "user's own uncommitted work\n")  # user's work preserved

    def test_7_new_error_in_changed_file_fails_normally(self):
        check = ("grep -q BROKEN src/cart.js && printf 'src/cart.js(1,1): error TS1005: expected.\\n'; "
                 "printf 'src/other.ts(3,1): error TS2304: Cannot find name y.\\n'; exit 1")
        rc, calls, r, repo = self.run_task(check, "BROKEN\n")
        self.assertEqual(rc, 1)
        self.assertGreaterEqual(len(calls), 2)                          # retried / escalated as before
        self.assertTrue(any("[FAILED_T1]" in e or "REPEATED_FAILURE" in e for e in r.escalations))
        self.assertEqual((repo / "src/dirty.js").read_text(), "user's own uncommitted work\n")


class Budgets(Base):
    def test_modes_differ(self):
        t = "implement job filter on the jobs page"
        self.assertNotIn("reviewer", roles(plan(t, "economy")))
        self.assertEqual(roles(plan(t, "balanced"), "conditional"), ["reviewer"])
        self.assertEqual(roles(plan("add loading state to submit button", "quality"), "required"), ["implementer", "reviewer"])

    def test_quality_is_not_t3(self):
        for e in EVAL:
            self.assertFalse(any(s["tier"] == 3 for s in plan(e[1], "quality")["steps"]), e[1])

    def test_micro_gets_no_quality_extras(self):
        self.assertEqual(roles(plan("change the border radius of the login button", "quality")), ["implementer"])


class StagePruning(Base):
    DEBUG_T = "find the root cause of an authentication race condition"

    def gate(self, out, task=None, planned=True):
        return m.architect_gate(out, plan(task or self.DEBUG_T), planned)

    def test_localized_skips_architect(self):
        run, codes, why = self.gate({"confidence": 0.96, "root_cause_candidate": "stale closure in onVisibilityChange", "relevant_files": ["hooks/s.ts:40"],
                                     "affected_subsystems": ["session"], "architecture_decision_required": False, "risk": "low"})
        self.assertFalse(run, why)

    def test_high_risk_bar_is_stricter(self):
        out = {"confidence": 0.85, "root_cause_candidate": "x", "relevant_files": ["a.ts"], "affected_subsystems": ["s"]}
        self.assertTrue(self.gate(out)[0])
        self.assertFalse(self.gate(out, "find the root cause of the footer layout bug")[0])

    def test_ambiguity_runs_architect_with_codes(self):
        cases = [({"confidence": 0.55, "root_cause_candidate": "x"}, "LOW_CONFIDENCE"),
                 ({"confidence": 0.96, "root_cause_candidate": "x", "root_causes": ["a", "b", "c"]}, "MULTIPLE_ROOT_CAUSES"),
                 ({"confidence": 0.96, "root_cause_candidate": "x", "architecture_decision_required": True}, "ARCHITECTURE_CHANGE"),
                 ({"confidence": 0.96, "root_cause_candidate": "x", "risk": "high"}, "SECURITY_RISK"),
                 ({"confidence": 0.96, "root_cause_candidate": "x", "unresolved_questions": ["?"]}, "CONFLICTING_EVIDENCE")]
        for out, code in cases:
            with self.subTest(code=code):
                run, codes, _ = self.gate(out)
                self.assertTrue(run); self.assertIn(code, codes)

    def test_critical_and_architecture_keep_architect(self):
        self.assertTrue(m.architect_gate({"confidence": 0.99}, plan("fix authentication race condition", root=WITH_AUTH), True)[0])
        self.assertTrue(m.architect_gate({"confidence": 0.99}, plan("redesign authentication architecture"), True)[0])


class PrunedExecution(Base):
    def run_debug(self, explorer_out, task="find and fix the root cause of the footer race condition"):
        calls = stub({"explorer": explorer_out, "architect": {"status": "done", "confidence": 0.9, "change_required": True, "next_action": "fix"},
                      "implementer": {"status": "no_change"}})
        m.Run("t", plan(task), NO_AUTH, {"commands": {}}).execute()
        return [c[0] for c in calls]

    def test_confident_explorer_goes_straight_to_implementer(self):
        self.assertEqual(self.run_debug({"status": "needs_change", "confidence": 0.96, "root_cause_candidate": "localized double subscribe",
                                         "relevant_files": ["src/cart.js:3"], "affected_subsystems": ["cart"], "next_action": "guard"}),
                         ["explorer", "implementer"])

    def test_uncertain_explorer_gets_architect(self):
        self.assertEqual(self.run_debug({"status": "needs_change", "confidence": 0.55, "root_causes": ["a", "b", "c"]}),
                         ["explorer", "architect", "implementer"])

    def test_researcher_only_when_asked(self):
        self.assertEqual(self.run_debug({"status": "needs_change", "confidence": 0.96, "root_cause_candidate": "localized", "relevant_files": ["src/cart.js"],
                                         "external_research_required": True}), ["explorer", "researcher", "implementer"])

    def test_downgrade_after_evidence(self):
        stub({"explorer": {"status": "needs_change", "confidence": 0.96, "root_cause_candidate": "one wrong conditional",
                           "relevant_files": ["src/cart.js"], "complexity": "MICRO"}, "implementer": {"status": "no_change"}})
        r = m.Run("t", plan("find and fix the root cause of the intermittent footer race condition"), NO_AUTH, {"commands": {}})
        r.execute()
        self.assertEqual(r.p["complexity"], "MICRO")
        self.assertEqual([e["tier"] for e in r.log if e["role"] == "implementer"], [1])


class Verification(Base):
    def checks(self, files, cx="SMALL", task="x", root=None):
        return m.select_checks(files, cx, task, CMDS, root)[0]

    def test_matrix(self):
        rel = git_repo({"src/Button.tsx": "x\n", "src/Button.test.tsx": "t\n"})
        cases = [([], "SMALL", []), (["README.md", "docs/a.png"], "SMALL", []), (["styles/a.css"], "SMALL", ["lint"]),
                 (["src/util.ts"], "MICRO", ["tc"]), (["src/a.ts", "src/b.ts"], "MEDIUM", ["tc", "lint", "test"]),
                 (["tsconfig.json"], "SMALL", ["tc", "lint", "build"]), (["package.json"], "SMALL", ["tc", "build"]),
                 (["src/util.ts"], "CRITICAL", ["tc", "lint", "test", "build"])]
        for files, cx, want in cases:
            with self.subTest(files=files, cx=cx):
                self.assertEqual(self.checks(files, cx), want)
        self.assertEqual(self.checks(["src/Button.tsx"], root=rel), ["tc", "test"])
        cmds, _, js = m.select_checks(["messages/en/home.json"], "SMALL", "x", CMDS)
        self.assertEqual((cmds, js), ([], ["messages/en/home.json"]))

    def test_explicit_request_honoured_but_not_for_docs(self):
        self.assertEqual(self.checks(["src/util.ts"], task="fix it and run tests"), ["tc", "test"])
        self.assertEqual(self.checks(["README.md"], task="fix typo in README tests section"), [])

    def test_no_change_skips_everything(self):
        stub({"implementer": {"status": "no_change"}})
        called = []
        m.verify = lambda *a: called.append(1) or (True, [])
        m.Run("t", plan("add loading state to submit button"), NO_AUTH, {"commands": CMDS}).execute()
        self.assertEqual(called, [])


class Escalation(Base):
    def test_bounded_retries_with_reason_codes(self):
        def edit(tier):
            (NO_AUTH / "src/cart.js").write_text(f"module.exports = {tier}{len(calls)}\n")
            return {"status": "done", "confidence": 0.9}
        calls = stub({"implementer": edit})
        m.verify = lambda *a: (False, [{"cmd": "tc", "ok": False, "tail": "src/cart.js(1,1): error TS2304: Cannot find name 'x'."}])
        r = m.Run("t", plan("add loading state to submit button"), NO_AUTH, {"commands": CMDS})
        r.execute()
        self.assertEqual([c[2] for c in calls], [1, 1, 2])   # T1 ×2 → [FAILED_T1] → T2; same failure a 3rd time → stop
        self.assertTrue(any("[FAILED_T1]" in e for e in r.escalations))
        self.assertTrue(any("[REPEATED_FAILURE]" in e for e in r.escalations))
        self.assertFalse(any(c[2] == 3 for c in calls))

    def test_t3_only_with_reason_code(self):
        calls = stub({"explorer": {"status": "needs_change", "confidence": 0.4, "root_causes": ["a", "b"]},
                      "architect": lambda t: {"status": "done", "confidence": 0.4 if t == 2 else 0.9, "change_required": False}})
        r = m.Run("t", plan("find the root cause of the footer race condition"), NO_AUTH, {"commands": {}})
        r.execute()
        self.assertEqual(len([c for c in calls if c[2] == 3]), 1)
        self.assertTrue(any("T2→T3 [LOW_CONFIDENCE]" in e for e in r.escalations))


class ReviewGate(Base):
    def gate(self, rel, body, outs=({"confidence": 0.9},), vres=({"ok": True},), planned=None, repaired=False):
        d = git_repo({rel: "x = 1\n"})
        (d / rel).write_text(body)
        return m.review_gate(d, [rel], plan("implement job filter on the jobs page"), list(outs), list(vres),
                             planned if planned is not None else [rel], repaired)

    def test_skips(self):
        self.assertFalse(self.gate("src/util.ts", "x = 2\n")[0])
        self.assertFalse(self.gate("README.md", "".join(f"line {i}\n" for i in range(200)))[0])

    def test_triggers(self):
        cases = {"auth": ("src/auth/session.ts", "x = 2\n", {}), "concurrency": ("src/util.ts", "await refresh()\n", {}),
                 "low confidence": ("src/util.ts", "x = 2\n", {"outs": ({"confidence": 0.4},)}), "unverified": ("src/util.ts", "x = 2\n", {"vres": ()}),
                 "off-plan": ("src/util.ts", "x = 2\n", {"planned": ["src/other.ts"]}), "public api": ("src/api/users.ts", "x = 2\n", {}),
                 "large": ("src/util.ts", "".join(f"y{i} = {i}\n" for i in range(80)), {}), "repaired": ("src/util.ts", "x = 2\n", {"repaired": True})}
        for name, (rel, body, kw) in cases.items():
            with self.subTest(name=name):
                self.assertTrue(self.gate(rel, body, **kw)[0])

    def test_economy_still_reviews_security(self):
        d = git_repo({"src/auth/session.ts": "x = 1\n"})
        (d / "src/auth/session.ts").write_text("x = 2\n")
        self.assertTrue(m.review_gate(d, ["src/auth/session.ts"], plan("implement job filter on the jobs page", "economy"), [{}], [{"ok": True}], [])[0])


class AggregateDiagnostics(Base):
    TSC_ERR = "src/a.ts(3,5): error TS2304: Cannot find name foo."

    def repo(self, scripts, extra=None):
        files = {"package.json": json.dumps({"name": "fx", "private": True, "scripts": scripts}), "src/a.ts": "x\n"}
        files.update(extra or {})
        return git_repo(files)

    def res(self, cat, status, state="required"):
        return {"cat": cat, "cmd": cat, "state": state, "status": status, "ok": status == "PASS", "errors": 0, "warnings": 0, "out": ""}

    def test_1_typecheck_fail_lint_pass_is_not_clean(self):
        self.assertEqual(m.overall_state([self.res("typecheck", "FAIL"), self.res("lint", "PASS")]), "DIRTY")

    def test_2_typecheck_pass_lint_fail_is_not_clean(self):
        self.assertEqual(m.overall_state([self.res("typecheck", "PASS"), self.res("lint", "FAIL")]), "DIRTY")

    def test_3_all_pass_is_clean(self):
        self.assertEqual(m.overall_state([self.res("typecheck", "PASS"), self.res("lint", "PASS"),
                                          dict(self.res("build", None, "deferred"), cmd="b")]), "CLEAN")

    def test_4_one_pass_with_another_unexecuted_is_not_clean(self):
        self.assertEqual(m.overall_state([self.res("lint", "PASS"), self.res("typecheck", None)]), "INCOMPLETE")
        ran = []
        r = m.Run("fix all errors", plan("fix all existing errors and warnings in this project"), NO_AUTH, {"commands": {}})
        rows = [{"cat": "lint", "cmd": "lint", "state": "required"}, {"cat": "typecheck", "cmd": "tc", "state": "required"}]
        out = r.run_diagnostics(rows, runner=lambda c: ran.append(c) or ((True, "") if c == "lint" else (False, self.TSC_ERR)))
        self.assertEqual(ran, ["lint", "tc"])                      # every required category is executed
        self.assertEqual(m.overall_state(out), "DIRTY")

    def test_5_mutating_lint_is_never_the_baseline(self):
        d = self.repo({"lint": "eslint . --fix"})
        self.assertEqual(m.safe_diag_commands(d, {})["lint"]["cmd"], "npx eslint .")
        d = self.repo({"lint": "eslint . --fix", "lint:check": "eslint ."})
        self.assertEqual(m.safe_diag_commands(d, {})["lint"]["cmd"], "npm run lint:check")
        d = self.repo({"lint": "eslint . --fix && prettier --write ."})
        self.assertIsNone(m.safe_diag_commands(d, {})["lint"]["cmd"])   # unsafe to derive → unavailable, not run
        d = self.repo({"typecheck": "tsc", "prettier": "prettier --write ."})
        cmds = m.safe_diag_commands(d, {})
        self.assertEqual((cmds["typecheck"]["cmd"], cmds["format"]["cmd"]), ("npx tsc --noEmit", "npx prettier --check ."))

    def test_5b_a_check_that_writes_files_is_restored(self):
        d = self.repo({"lint": "printf changed > src/a.ts"})
        r = m.Run("fix all errors", plan("fix all existing errors and warnings in this project", root=d), d, {"commands": {}})
        r.before, r.pre = m.snapshot(d), {}
        out = r.run_diagnostics([{"cat": "lint", "cmd": "printf changed > src/a.ts", "state": "required"}])
        self.assertEqual((d / "src/a.ts").read_text(), "x\n")
        self.assertIn("restored", out[0]["note"])

    def test_6_broad_maintenance_enables_aggregate(self):
        for t in ("Fix all existing errors and warnings in this project", "fix all TypeScript and lint errors", "make the project pass checks",
                  "исправь все ошибки в проекте", "repair all current static-analysis issues"):
            with self.subTest(t=t):
                p = plan(t)
                self.assertTrue(p["signals"]["aggregate"])
        rows = {d["cat"]: d for d in plan("Fix all existing errors and warnings in this project")["diagnostics"]}
        self.assertEqual([(rows[c]["need"], rows[c]["state"]) for c in ("typecheck", "lint")], [("required", "run")] * 2)
        self.assertEqual((rows["test"]["need"], rows["build"]["need"], rows["build"]["state"]), ("optional", "optional", "deferred"))

    def test_7_targeted_task_disables_aggregate(self):
        for t in ("fix this button", "add loading state to submit button", "fix the login form error message"):
            with self.subTest(t=t):
                self.assertFalse(plan(t)["signals"]["aggregate"])

    def test_8_existing_typescript_errors_start_the_pipeline(self):
        d = git_repo({"src/a.ts": "x\n"})
        commands = {"typecheck": f"test -f fixed || {{ printf '{self.TSC_ERR}\\n'; exit 2; }}", "lint": "true"}
        def impl(tier):
            (d / "fixed").write_text("y\n")
            return {"status": "done", "confidence": 0.9}
        calls = stub({"implementer": impl})
        r = m.Run("Fix all existing errors and warnings in this project", plan("Fix all existing errors and warnings in this project", root=d),
                  d, {"commands": commands})
        rc = r.execute()
        self.assertEqual([c[0] for c in calls], ["implementer"])    # not "nothing to fix"
        self.assertEqual(rc, 0)                                      # all required categories clean afterwards

    def test_8b_required_unavailable_blocks_before_any_model_call(self):
        d = self.repo({"typecheck": "tsc --noEmit", "lint": "eslint . --fix && prettier --write ."})   # no safe read-only lint form
        def impl(tier):
            (d / "fixed").write_text("y\n")
            return {"status": "done", "confidence": 0.9}
        stub({"implementer": impl})
        task = "Fix all existing errors and warnings in this project"
        r = m.Run(task, plan(task, root=d), d, {"package_manager": "npm"})
        r.before, r.pre = m.snapshot(d), {}
        runner = lambda c: (True, "") if (d / "fixed").exists() else (False, self.TSC_ERR)  # noqa: E731 — no package manager needed
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = r.aggregate_repair({x["role"]: x for x in r.p["steps"] if x["provider"]}, "", runner=runner)
        out = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("BLOCKED ·", out); self.assertNotIn("DONE ·", out)
        self.assertIn("Verify:      BLOCKED", out)                   # never "NOT DONE"/"DONE" next to "Verify: PASS"
        self.assertIn("REQUIRED_EVIDENCE_UNAVAILABLE: LINT", out)
        self.assertFalse((d / "fixed").exists())                      # no implementer call: a required source can't be read

    def test_misconfigured_check_is_not_a_code_error_and_not_clean(self):
        d = self.repo({"typecheck": "true", "prettier": "prettier --check \"{src,store}/**/*.js\""})
        r = m.Run("fix all errors", plan("fix all existing errors and warnings in this project", root=d), d, {"commands": {}})
        r.before, r.pre = m.snapshot(d), {}
        out = r.run_diagnostics([{"cat": "format", "cmd": "printf '[error] No files matching the pattern were found: x' >&2; exit 2", "state": "required"},
                                 {"cat": "typecheck", "cmd": "true", "state": "required"}])
        self.assertEqual(out[0]["status"], "BROKEN")
        self.assertEqual(m.overall_state(out), "INCOMPLETE")   # never "clean", but no model is sent to fix formatting
        self.assertEqual(m.group_diags(out), [])

    def test_grouping_is_by_root_cause(self):
        out = "\n".join([f"src/f{i}.ts(1,1): error TS2322: Type 'string' is not assignable to type 'number'." for i in range(5)]
                        + ["src/g.ts(2,2): error TS2304: Cannot find name 'x'."])
        groups = m.group_diags([dict(self.res("typecheck", "FAIL"), out=out)])
        self.assertEqual([(g["count"], len(g["files"])) for g in groups], [(5, 5), (1, 1)])


class BatchRegression(Base):
    def run_regression(self, improve_first):
        root = git_repo({"src/a.ts": "committed\n", "src/dirty.txt": "original\n"})
        (root / "src/a.ts").write_text("user-baseline\n")
        (root / "src/dirty.txt").write_text("user work\n")
        edits = []
        def impl(tier):
            edits.append(tier)
            (root / "src/a.ts").write_text("improved\n" if improve_first and len(edits) == 1 else "regressed\n")
            return {"status": "done", "confidence": 0.9}
        stub({"implementer": impl})
        task = "fix all errors"
        r = m.Run(task, plan(task, root=root), root, {"commands": {"typecheck": "fixture-check"}})
        r.before = m.snapshot(root)
        r.pre = {f: (root / f).read_bytes() for f in r.before}
        def check(command):
            n = {"user-baseline": 16, "improved": 10, "regressed": 37}[(root / "src/a.ts").read_text().strip()]
            return False, "\n".join("src/a.ts(1,1): error TS2304: Missing name foo." for _ in range(n))
        steps = {x["role"]: x for x in r.p["steps"] if x["provider"]}
        self.assertEqual(r.aggregate_repair(steps, "", runner=check), 1)
        self.assertEqual((root / "src/a.ts").read_text(), "improved\n" if improve_first else "user-baseline\n")
        self.assertEqual((root / "src/dirty.txt").read_text(), "user work\n")
        self.assertEqual(len(edits), 2 if improve_first else 1)
        self.assertNotIn(3, edits)
        self.assertFalse(r.escalations)

    def test_16_to_37_restores_batch_and_stops(self):
        self.run_regression(False)

    def test_previous_successful_batch_is_preserved(self):
        self.run_regression(True)

    def test_mutating_check_restores_dirty_user_file(self):
        root = git_repo({"src/a.ts": "committed\n"})
        (root / "src/a.ts").write_text("user dirty work\n")
        r = m.Run("fix all errors", plan("fix all errors", root=root), root, {"commands": {}})
        r.pre = {"src/a.ts": b"user dirty work\n"}
        out = r.run_diagnostics([{"cat": "lint", "cmd": "printf changed > src/a.ts", "state": "required"}])
        self.assertEqual((root / "src/a.ts").read_text(), "user dirty work\n")
        self.assertEqual(out[0]["status"], "BROKEN")

    def test_warning_and_blocked_statuses(self):
        ok, rows = REAL_VERIFY(NO_AUTH, ["printf 'warning: check this\\n'"], [], None)
        self.assertTrue(ok)
        self.assertEqual(m.verify_status(rows), "PASS_WITH_WARNINGS")
        self.assertEqual(m.verify_status([{"ok": False, "status": "BLOCKED"}]), "BLOCKED")


def run_aggregate(root, task, runner, rmap=None):
    """aggregate_repair with an injected check runner (no package manager needed). → (rc, stdout)."""
    r = m.Run(task, plan(task, root=root), root, rmap or {"package_manager": "npm"})
    r.before, r.pre = m.snapshot(root), {}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = r.aggregate_repair({x["role"]: x for x in r.p["steps"] if x["provider"]}, "", runner=runner)
    return rc, buf.getvalue()


class ReadOnlyDerivation(Base):
    def cmds(self, scripts, pm="npm"):
        root = git_repo({"package.json": json.dumps({"name": "fx", "private": True, "scripts": scripts})})
        return m.safe_diag_commands(root, {"package_manager": pm})

    def test_eslint_fix_derives_read_only_lint(self):
        c = self.cmds({"lint": "eslint \"**/*.+(ts|tsx)\" --fix --ignore-pattern 'node_modules/'"}, "yarn")["lint"]  # quoted | is not a pipe
        self.assertEqual(c["cmd"], "yarn eslint '**/*.+(ts|tsx)' --ignore-pattern node_modules/")
        self.assertEqual((c["configured_command"], c["derived_read_only_command"], c["confidence"]), ("yarn lint", c["cmd"], "high"))
        self.assertIn("--fix", c["derivation_reason"])
        self.assertEqual(self.cmds({"lint": "eslint . --fix-type problem --fix"})["lint"]["cmd"], "npx eslint .")

    def test_prettier_write_derives_check(self):
        self.assertEqual(self.cmds({"format": "prettier --write src"})["format"]["cmd"], "npx prettier --check src")
        c = self.cmds({"prettier": "prettier --ignore-path .gitignore \"**/*.+(ts|tsx)\"", "format": "yarn run prettier --write"}, "yarn")["format"]
        self.assertEqual(c["cmd"], "yarn prettier --ignore-path .gitignore '**/*.+(ts|tsx)' --check")   # script references resolved
        self.assertEqual(m.read_only_form("yarn format", {"format": "yarn run prettier --write", "prettier": "prettier src"}, "yarn")["cmd"],
                         "yarn prettier src --check")

    def test_stylelint_fix_derives_read_only(self):
        c = self.cmds({"lint:css": "stylelint \"src/**/*.css\" --fix"})["style"]
        self.assertEqual((c["cmd"], c["confidence"]), ("npx stylelint 'src/**/*.css'", "high"))

    def test_unknown_mutating_command_is_unavailable(self):
        c = self.cmds({"lint": "mylinter --fix src"})["lint"]
        self.assertIsNone(c["cmd"]); self.assertEqual(c["confidence"], "none"); self.assertIn("not in the read-only allowlist", c["derivation_reason"])
        self.assertIsNone(m.read_only_form("tsc -b")["cmd"])                                # no read-only equivalent
        self.assertEqual(m.read_only_form("tsc -p tsconfig.app.json")["cmd"], "tsc -p tsconfig.app.json --noEmit")

    def test_already_read_only_command_is_unchanged(self):
        for scripts, cat, cmd in (({"lint": "eslint ."}, "lint", "npm run lint"),
                                  ({"check-format": "prettier --list-different .", "format": "prettier --write ."}, "format", "npm run check-format"),
                                  ({"typecheck": "tsc --noEmit -p tsconfig.app.json"}, "typecheck", "npm run typecheck")):
            with self.subTest(cat=cat):
                c = self.cmds(scripts)[cat]
                self.assertEqual((c["cmd"], c["derived_read_only_command"], c["confidence"]), (cmd, None, "exact"))

    def test_dangerous_shell_operators_are_not_derived(self):
        for body in ("eslint . --fix; rm -rf dist", "eslint . --fix && prettier --write .", "eslint . --fix | tee log",
                     "eslint $(git ls-files) --fix", "eslint `ls` --fix", "eslint . --fix > out.txt"):
            with self.subTest(body=body):
                self.assertIsNone(self.cmds({"lint": body})["lint"]["cmd"])
                self.assertIsNone(m.read_only_form(body)["cmd"])
        self.assertEqual(m.read_only_form("tsc --noEmit && vitest run")["cmd"], "tsc --noEmit && vitest run")  # not mutating: as configured

    def test_verification_never_runs_a_mutating_script(self):
        root = git_repo({"package.json": json.dumps({"scripts": {"lint": "eslint . --fix", "format": "mytool --write"}})})
        cmds = m.Run("t", plan("add loading state to submit button", root=root), root,
                     {"commands": {"lint": "npm run lint", "format": "npm run format", "typecheck": "tc"}}).cmds
        self.assertEqual(cmds, {"lint": "npx eslint .", "typecheck": "tc"})


class GeneratedOutput(Base):
    SRC_ERR = 'src/styles/app.css\n  1:4  ✖  Unexpected unknown property "colr"  property-no-unknown'
    GEN_WARN = 'dist-dev/assets/index.css\n  1:1  ⚠  Unexpected vendor-prefixed property "-webkit-box"  property-no-vendor-prefix'
    TASK = "fix all errors and warnings in this project"

    def repo(self):
        root = git_repo({".gitignore": "dist-*\n", "src/styles/app.css": ".a{colr:red}\n", "build/gen.js": "module.exports = 1\n",
                         "vite.config.ts": "export default { build: { outDir: `dist-${mode}` } }\n",
                         "package.json": json.dumps({"scripts": {"lint:css": "stylelint \"**/*.css\""}})})
        (root / "dist-dev/assets").mkdir(parents=True)
        (root / "dist-dev/assets/index.css").write_text(".b{-webkit-box:1}\n")
        (root / ".idea").mkdir()
        return root

    def runner(self, root):
        def check(cmd):
            src_bad = "colr" in (root / "src/styles/app.css").read_text()
            return (not src_bad), ((self.SRC_ERR + "\n") if src_bad else "") + self.GEN_WARN
        return check

    def test_generated_dirs_need_evidence(self):
        root = self.repo()
        self.assertEqual(m.generated_dirs(root), {"dist-dev": "git-ignored, output in vite.config.ts"})   # tracked build/ is source
        mp = m.detect_map(root)
        self.assertIn("dist-dev", mp["generated_dirs"]); self.assertNotIn("dist-dev", mp["dirs"])
        is_gen = m.generated_classifier(root)
        self.assertTrue(is_gen("dist-dev/assets/index.css")); self.assertTrue(is_gen(str(root / "dist-dev/a.css")))
        self.assertFalse(is_gen("src/styles/app.css")); self.assertFalse(is_gen("build/gen.js"))

    def test_generated_only_ide_warnings_are_classified_separately(self):
        root = self.repo()
        (root / "src/styles/app.css").write_text(".a{color:red}\n")
        rc, out = run_aggregate(root, self.TASK + " (WebStorm flags dist-dev/assets/index.css)", self.runner(root))  # forbid(): no model call
        self.assertEqual(rc, 0)
        self.assertIn("only in generated/vendor output", out)
        self.assertIn("IDE_ONLY   dist-dev/assets/index.css: generated output named in the task", out)
        self.assertIn("mark dist-dev/ as Excluded in the IDE (local setting; .idea is not tracked, nothing written)", out)
        self.assertIn("DONE", out); self.assertIn("Verify:      PASS_WITH_WARNINGS", out)
        self.assertFalse(list((root / ".idea").iterdir()))

    def test_source_css_issue_still_triggers_implementation(self):
        root, prompts = self.repo(), []
        def run(provider, tier, prompt, write, role, tdir, label, allow, **k):
            prompts.append(prompt)
            (root / "src/styles/app.css").write_text(".a{color:red}\n")
            return json.dumps({"status": "done", "confidence": 0.9}), {"in": 1, "cached": 0, "out": 1, "usd": None}, "stub"
        m.run_provider = run
        rc, out = run_aggregate(root, self.TASK, self.runner(root))
        self.assertEqual((rc, len(prompts)), (0, 1))
        self.assertIn("- src/styles/app.css", prompts[0]); self.assertNotIn("- dist-dev", prompts[0])
        self.assertIn("Never edit generated output (dist-dev/)", prompts[0])

    def test_generated_dist_css_is_not_edited(self):
        root = self.repo()
        def run(provider, tier, prompt, write, role, tdir, label, allow, **k):
            (root / "dist-dev/assets/index.css").write_text(".b{display:flex}\n")   # agent "fixes" the build output too
            (root / "dist-dev/assets/new.css").write_text("x\n")
            (root / "src/styles/app.css").write_text(".a{color:red}\n")
            return json.dumps({"status": "done", "confidence": 0.9}), {"in": 1, "cached": 0, "out": 1, "usd": None}, "stub"
        m.run_provider = run
        rc, out = run_aggregate(root, self.TASK, self.runner(root))
        self.assertEqual(rc, 0)
        self.assertEqual((root / "dist-dev/assets/index.css").read_text(), ".b{-webkit-box:1}\n")
        self.assertFalse((root / "dist-dev/assets/new.css").exists())
        self.assertIn("implementer edited generated output", out); self.assertIn("generated-file edit(s) reverted", out)


class RequiredOptional(Base):
    def repo(self, scripts):
        return git_repo({"package.json": json.dumps({"scripts": scripts}), "src/a.js": "x\n"})

    def test_optional_unavailable_diagnostic_does_not_block(self):
        root = self.repo({"lint": "eslint .", "format": "mytool --write ."})           # format: no safe read-only form
        rc, out = run_aggregate(root, "fix all existing errors and warnings in this project", lambda c: (True, ""))
        self.assertEqual(rc, 0)
        self.assertIn("UNAVAILABLE — `format` modifies files", out); self.assertIn("warning, does not block", out)
        self.assertIn("DONE", out); self.assertNotIn("NOT DONE", out)
        self.assertIn("Verify:      PASS_WITH_WARNINGS", out); self.assertIn("FORMAT unavailable (optional)", out)

    def test_required_unavailable_diagnostic_blocks(self):
        root = self.repo({"lint": "mylinter --fix src", "format": "prettier --check ."})
        rc, out = run_aggregate(root, "fix all existing errors and warnings in this project", lambda c: (True, ""))
        self.assertEqual(rc, 1)
        self.assertIn("BLOCKED ·", out); self.assertIn("Verify:      BLOCKED", out); self.assertIn("REQUIRED_EVIDENCE_UNAVAILABLE: LINT", out)

    def test_nothing_runnable_is_never_done(self):
        root = self.repo({"format": "mytool --write ."})                                # only an optional gap, nothing ran
        rc, out = run_aggregate(root, "fix all existing errors and warnings in this project", lambda c: (True, ""))
        self.assertEqual(rc, 1); self.assertIn("Verify:      BLOCKED", out)


class Security(Base):
    def test_redaction(self):
        for secret in ("sk-ant-api03-" + "a" * 30, "AKIA" + "A" * 16, "ghp_" + "b" * 36, "Bearer " + "c" * 40, "password=hunter22xyz",
                       "-----BEGIN " + "RSA PRIVATE KEY-----\nabcdefghij\n-----END " + "RSA PRIVATE KEY-----", "AIza" + "d" * 35):
            with self.subTest(secret=secret[:12]):
                self.assertNotIn(secret.split()[-1][-10:], m.redact(f"log {secret} end"))

    def test_state_files_are_redacted(self):
        stub({"implementer": {"status": "no_change"}})
        r = m.Run("set api_key=sk-" + "z" * 30, plan("add loading state to submit button"), NO_AUTH, {"commands": {}})
        r.execute()
        for f in r.dir.iterdir():
            self.assertNotIn("z" * 30, f.read_text(), f.name)

    def test_claude_workers_cannot_read_env_files(self):
        captured = {}

        def fake_run(cmd, **k):
            captured["cmd"], captured["cwd"] = cmd, k.get("cwd")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "{}", "usage": {}}), "")
        real = m.subprocess.run
        m.subprocess.run = fake_run
        try:
            REAL_RUN_PROVIDER("claude", 1, "x", True, "implementer", Path(tempfile.mkdtemp()), "t", [], cwd="/repo")
        finally:
            m.subprocess.run = real
            m.MODEL_CALLS[0] -= 1  # the command was captured, not executed
        self.assertIn("Read(./.env)", captured["cmd"])
        self.assertEqual(captured["cwd"], "/repo")
        self.assertIn("--setting-sources", captured["cmd"])

    def test_state_retention(self):
        root = git_repo({"a.txt": "x\n"})
        d = root / ".ai" / "state" / "tasks"
        for i in range(m.LIM["retain_tasks"] + 5):
            (d / f"T2026010{i:02d}").mkdir(parents=True)
        m.prune_state(root)
        self.assertEqual(len(list(d.iterdir())), m.LIM["retain_tasks"])


if __name__ == "__main__":
    unittest.main()
