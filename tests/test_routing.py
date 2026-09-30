"""ai-team router regression suite: classification → one native agent at the cheapest capable tier → fallback, retry and
escalation only on failure. No model calls: CLIs are faked and providers are stubbed.

Run: ai-team --self-test      (or: python3 -m unittest discover -s ~/.ai-kit/tests)
"""
import contextlib, importlib.machinery, importlib.util, io, json, os, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

SRC = Path(os.environ.get("AI_KIT", str(Path(__file__).resolve().parents[1]))) / "bin" / "ai-team"
loader = importlib.machinery.SourceFileLoader("ai_team_under_test", str(SRC))
spec = importlib.util.spec_from_loader("ai_team_under_test", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

# Isolation: fake installed CLIs (codex + claude), no history, no real cooldowns, no metric writes, no real calls, no sleeping.
m.shutil.which = lambda b: f"/usr/bin/{b}" if b in ("codex", "claude") else None
m.metrics = lambda: []
m._HIST = []
m.record = lambda *a, **k: None
m.COOLDOWN_FILE = Path(tempfile.mkdtemp()) / "cooldown.json"
m._AVAIL.clear()
m.SLEEP = lambda s: None


def forbid(*a, **k):
    raise AssertionError("a real provider call was attempted")


REAL_RUN_PROVIDER = m.run_provider
REAL_VERIFY = m.verify
m.run_provider = forbid


def git_repo(files, after=None):
    d = Path(tempfile.mkdtemp(prefix="router repo "))
    for rel, body in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body if isinstance(body, str) else json.dumps(body))
    subprocess.run("git init -q && git add -A && git -c user.email=t@t -c user.name=t commit -qm init", shell=True, cwd=d, check=True)
    for rel, body in (after or {}).items():  # untracked / ignored files created after the commit
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    return d


CMDS = {"typecheck": "tc", "lint": "lint", "test": "test", "build": "build"}
NO_AUTH = git_repo({"src/cart.js": "module.exports = 1\n", "README.md": "# x\n",
                    "package.json": '{"scripts":{"typecheck":"true","lint":"true","test":"true","build":"true"}}'})
WITH_AUTH = git_repo({"src/auth/session.ts": "export const session = {}\n"})


def plan(task, budget="balanced", root=NO_AUTH, forced=None, apply=False):
    return m.plan(task, budget, forced, root, apply)


def roles(p, stage=None):
    return [s["role"] for s in p["steps"] if stage is None or s["stage"] == stage]


def stub(responses):
    """run_provider stub: responses[role] is the agent's status line (dict), a callable(tier) → dict, or a raw final message
    (str). Records (role, provider, tier, prompt, write)."""
    calls = []

    def run(provider, tier, prompt, write, role, tdir, label, cwd=None, research=False):
        calls.append((role, provider, tier, prompt, write))
        r = responses.get(role, {"status": "done", "summary": "ok"})
        r = r(tier) if callable(r) else r
        return (r if isinstance(r, str) else "Done.\n" + json.dumps(r)), {"in": 1, "cached": 0, "out": 1, "usd": None}, "stub"
    m.run_provider = run
    return calls


def execute(task, root=NO_AUTH, rmap=None, budget="balanced"):
    """plan → local answer or Run.execute, stdout captured → (rc, stdout, run or None)."""
    p = plan(task, budget, root)
    buf, r = io.StringIO(), None
    with contextlib.redirect_stdout(buf):
        if p.get("local"):
            rc = m.local(p, root, m.load_map(root), task)
        else:
            r = m.Run(task, p, root, rmap if rmap is not None else {"commands": {}})
            rc = r.execute()
    return rc, buf.getvalue(), r


class Base(unittest.TestCase):
    def setUp(self):
        m._HIST = []
        m.BROKEN.clear()
        m._AVAIL.clear()
        m.run_provider = forbid
        m.verify = lambda *a: (True, [])
        m.COOLDOWN_FILE.write_text("{}")
        os.environ.pop("AI_TEAM_PROVIDERS", None)
        subprocess.run("git checkout -q . && git clean -qfd -e .ai", shell=True, cwd=NO_AUTH)


EVAL = [  # (id, task, intent, complexity or None, role (None = T0 local answer), edits?, starting tier)
    (1, "ты работаешь?", "META", "TRIVIAL", None, False, None),
    (2, "show providers", "META", "TRIVIAL", None, False, None),
    (3, "explain what this component does", "QUESTION", "SMALL", "explorer", False, 1),
    (4, "where is authentication configured?", "QUESTION", "SMALL", "explorer", False, 1),
    (5, "what is a race condition?", "QUESTION", "SMALL", "answer", False, 1),
    (6, "change the border radius of the login button", "IMPLEMENTATION", "MICRO", "implementer", True, 1),
    (7, "fix typo in README", "IMPLEMENTATION", "MICRO", "implementer", True, 1),
    (8, "add loading state to submit button", "IMPLEMENTATION", "SMALL", "implementer", True, 1),
    (9, "find why this component renders twice", "DEBUG", None, "explorer", False, 1),
    (10, "find and fix an authentication race condition", "DEBUG", "COMPLEX", "implementer", True, 2),
    (11, "redesign authentication architecture", "ARCHITECTURE", "COMPLEX", "architect", False, 2),
    (12, "review my current changes", "REVIEW", None, None, False, None),   # clean tree: nothing to review
    (13, "check whether dependency X changed its API in the latest version", "RESEARCH", None, "researcher", False, 1),
    (14, "fix all current TypeScript errors", "DEBUG", "MEDIUM", "implementer", True, 2),
    (15, "tell me what build command this project uses", "META", "TRIVIAL", None, False, None),
    (16, "plan how to add pagination to the list page", "PLAN", None, "architect", False, 1),
    (17, "how should we implement pagination?", "PLAN", None, "architect", False, 1),
    (18, "clean up the codebase", "IMPLEMENTATION", "MEDIUM", "implementer", True, 2),
    (19, "clean up the project?", "QUESTION", None, "explorer", False, 1),
    (20, "fix all WebStorm errors", "DEBUG", "MEDIUM", "implementer", True, 2),
    (21, "fix all Sonar issues", "IMPLEMENTATION", "MEDIUM", "implementer", True, 2),
    (22, "the dark mode toggle for the settings page", "UNKNOWN", "SMALL", "implementer", True, 1),
]


class Eval(Base):
    def test_scenarios(self):
        for i, task, intent, cx, role, write, tier in EVAL:
            with self.subTest(i=i, task=task):
                p = plan(task)
                self.assertEqual(p["intent"], intent)
                if cx:
                    self.assertEqual(p["complexity"], cx)
                if role is None:
                    self.assertTrue(p.get("local")); self.assertEqual(p["steps"], [])
                    continue
                self.assertEqual(roles(p, "required"), [role])                    # ONE agent does the task
                self.assertEqual((p["steps"][0]["write"], p["steps"][0]["tier"]), (write, tier))
                self.assertFalse(any(s["tier"] == 3 for s in p["steps"]), "T3 only by escalation")
                self.assertTrue(set(roles(p)) <= {role, "reviewer"})              # no explorer/architect/judge pipeline

    def test_critical_needs_repo_evidence(self):
        self.assertEqual(plan("fix authentication race condition")["complexity"], "COMPLEX")
        p = plan("fix authentication race condition", root=WITH_AUTH)
        self.assertEqual(p["complexity"], "CRITICAL")
        self.assertEqual((roles(p, "required"), p["steps"][0]["tier"]), (["implementer"], 2))
        self.assertTrue(p["steps"][1]["likely"])                                  # CRITICAL → the independent review will run

    def test_no_tool_specific_logic(self):
        base = plan("fix all errors")
        for tool in ("WebStorm", "Sonar", "Jira", "VS Code", "Qodana", "CI", "Stylelint"):
            with self.subTest(tool=tool):
                p = plan(f"fix all {tool} errors")
                s = p["steps"][0]
                self.assertEqual((s["role"], s["tier"], s["provider"]), (base["steps"][0]["role"], base["steps"][0]["tier"], base["steps"][0]["provider"]))
                self.assertTrue(m.task_prompt("implementer", f"fix all {tool} errors", NO_AUTH, True).startswith(f"fix all {tool} errors"))
        self.assertFalse(hasattr(m, "DIAG"))
        self.assertFalse((m.KIT / "diagnostics").exists())


class ZeroToken(Base):
    def test_local_paths_make_no_calls(self):
        for t in ("ты работаешь?", "show providers", "какая модель будет использована?", "tell me what build command this project uses",
                  "git status", "что ты будешь делать?"):
            with self.subTest(t=t):
                p = plan(t)
                self.assertTrue(p.get("local"))
                with contextlib.redirect_stdout(io.StringIO()):
                    m.local(p, NO_AUTH, m.load_map(NO_AUTH), t)  # forbid() raises if a provider is touched

    def test_plan_and_context_plan_make_no_calls(self):
        calls = m.MODEL_CALLS[0]
        for t in ("find and fix an authentication race condition", "explain what this component does", "fix all WebStorm errors",
                  "what is a race condition?"):
            p = plan(t)
            with contextlib.redirect_stdout(io.StringIO()):
                m.print_plan(p, NO_AUTH, {}, t)
                m.print_context_plan(p, NO_AUTH, {}, t)
        self.assertEqual(m.MODEL_CALLS[0], calls)


class PromptMinimal(Base):
    def test_prompt_is_the_original_task_plus_a_few_router_lines(self):
        root = git_repo({".ai/CONTEXT.md": "# P\n\n## Project facts\n" + "fact\n" * 200 + "\n## Rules\n- r\n"})
        task = "add a dark mode toggle to the settings page"
        for role, write in (("implementer", True), ("explorer", False), ("architect", False), ("researcher", False), ("reviewer", False)):
            with self.subTest(role=role):
                p = m.task_prompt(role, task, root, write)
                self.assertTrue(p.startswith(task + "\n"))
                self.assertLessEqual(m.tok(p) - m.tok(task), 110)                    # router lines only
                self.assertNotIn("fact", p); self.assertNotIn("# Role:", p)          # no project dump, no role essay
        self.assertEqual(m.task_prompt("answer", "what is a race condition?"), "what is a race condition?")
        self.assertIn("Read-only", m.task_prompt("explorer", task, root))
        self.assertIn('"issues"', m.task_prompt("reviewer", task, root))
        self.assertIn("Never edit generated output (dist-dev/)", m.task_prompt("implementer", task, root, True, gdirs=["dist-dev"]))

    def test_plan_context_is_small_and_stable(self):
        for i, task, *_ in EVAL:
            p = plan(task)
            for s in p["steps"]:
                with self.subTest(i=i, role=s["role"]):
                    self.assertLessEqual(s["ctx"] - m.tok(task), 110)
        a, b = m.task_prompt("implementer", "task a", NO_AUTH, True), m.task_prompt("implementer", "task a", NO_AUTH, True)
        self.assertEqual(a, b); self.assertNotRegex(a, r"20\d\d-\d\d-\d\d|T20\d{6}")

    def test_permanent_kit_files_stay_small(self):
        for f in (m.KIT / "roles").glob("*.md"):
            with self.subTest(f=f.name):
                self.assertLessEqual(len(f.read_text()), 600 if f.stem == "orchestrator" else 400)
        self.assertLessEqual(len((m.KIT / "template" / "CONTEXT.md").read_text()), 1200)
        for s in (m.WRITE_STATUS, m.READ_STATUS, m.REVIEW_STATUS):
            self.assertLessEqual(len(s), 300)

    def test_parse_status(self):
        out = m.parse_status("Renamed x to y in a.js.\n{\"status\":\"done\",\"summary\":\"renamed\",\"files\":[\"a.js\"]}", "implementer")
        self.assertEqual((out["status"], out["summary"], out["files"], out["answer"]), ("done", "renamed", ["a.js"], "Renamed x to y in a.js."))
        fenced = m.parse_status("The router is in bin/ai-team.\n```json\n{\"status\":\"done\",\"summary\":\"found\"}\n```", "explorer")
        self.assertEqual((fenced["status"], fenced["answer"]), ("done", "The router is in bin/ai-team."))
        prose = m.parse_status("Just prose, no status line.", "explorer")
        self.assertEqual((prose["status"], prose["answer"], prose["_unstructured"]), ("done", "Just prose, no status line.", True))
        self.assertEqual(m.parse_status("{not json", "answer"), {"status": "done", "answer": "{not json"})


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
        with contextlib.redirect_stdout(io.StringIO()):
            m.Run("t", p, NO_AUTH, {"commands": {}}).execute()
        self.assertEqual(calls, ["codex", "claude"])


class ProviderFailover(Base):
    """Native agent CLIs behind a claude-code-router-style fallback chain: classify the failure, then retry, cool down or
    disable the provider, and hand the call to the next capable one."""

    def setUp(self):
        super().setUp()
        self.sleeps = []
        m.SLEEP = self.sleeps.append
        self.addCleanup(setattr, m, "SLEEP", lambda s: None)

    def run_with(self, script, task="add loading state to submit button"):
        """script: {provider: [outcome, …]} consumed per call; a str outcome is a CLI failure message, a dict the agent's status."""
        calls, queue = [], {k: list(v) for k, v in script.items()}

        def run(provider, tier, prompt, write, role, *a, **k):
            calls.append((role, provider, tier, prompt))
            nxt = queue[provider].pop(0) if queue.get(provider) else {"status": "no_change"}
            if isinstance(nxt, str):
                raise m.ProviderError(nxt)
            return json.dumps(nxt), {"in": 1, "cached": 0, "out": 1, "usd": None}, "stub"
        m.run_provider = run
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = m.Run(task, plan(task), NO_AUTH, {"commands": {}})
            rc = r.execute()
        return rc, calls, buf.getvalue(), r

    def test_failure_classes(self):
        for text, kind in (("You've hit your usage limit. Try again in 2 hours.", "rate_limit"), ("HTTP 429 Too Many Requests", "rate_limit"),
                           ("Claude AI usage limit reached|1759999999", "rate_limit"), ("Invalid API key · Please run /login", "auth"),
                           ("unexpected status 401 Unauthorized", "auth"), ("error_max_budget_usd", "budget"), ("error_max_turns", "budget"),
                           ("API Error: 529 overloaded_error", "transient"), ("stream disconnected before completion", "transient"),
                           ("503 Service Unavailable", "transient"), ("model not found", "fatal")):
            with self.subTest(text=text):
                self.assertEqual(m.failure_kind(text), kind)

    def test_cooldown_and_backoff_follow_the_provider(self):
        now = m.datetime.datetime(2026, 9, 30, 10, 0)
        self.assertEqual(m.cooldown_minutes("try again in 1 day 3 hours 32 minutes", now), 1652)
        self.assertEqual(m.cooldown_minutes("5-hour limit reached · resets 3pm", now), 300)
        self.assertEqual(m.cooldown_minutes(f"usage limit reached|{int(now.timestamp()) + 5400}", now), 90)
        self.assertEqual(m.cooldown_minutes("quota exceeded", now), 60)
        self.assertEqual((m.retry_delay("please retry after 3 seconds", 1), m.retry_delay("503", 1), m.retry_delay("503", 9)), (3, 1, 30))

    def test_transient_failure_is_retried_on_the_same_provider(self):
        rc, calls, out, _ = self.run_with({"codex": ["503 Service Unavailable", {"status": "no_change"}]})
        self.assertEqual([c[1] for c in calls], ["codex", "codex"]); self.assertEqual(self.sleeps, [1])
        self.assertEqual(rc, 0, out); self.assertNotIn("codex", m.BROKEN)

    def test_rate_limit_cools_down_and_falls_back(self):
        rc, calls, out, _ = self.run_with({"codex": ["You've hit your usage limit. Try again in 2 hours."]})
        self.assertEqual([c[1] for c in calls], ["codex", "claude"]); self.assertEqual(rc, 0, out)
        until = m.datetime.datetime.fromisoformat(json.loads(m.COOLDOWN_FILE.read_text())["codex"])
        self.assertAlmostEqual((until - m.datetime.datetime.now()).total_seconds() / 60, 120, delta=2)
        self.assertIn("Fallbacks:   implementer-t1a1 codex T1 rate_limit", out)
        rc, calls, out, _ = self.run_with({})                                     # the next task skips the provider in cooldown
        self.assertEqual({c[1] for c in calls}, {"claude"})

    def test_auth_error_disables_the_provider_but_a_budget_limit_only_skips_the_call(self):
        rc, calls, out, _ = self.run_with({"codex": ["Invalid API key · Please run /login"]})
        self.assertEqual(([c[1] for c in calls], rc), (["codex", "claude"], 0)); self.assertIn("codex", m.BROKEN)
        m.BROKEN.clear(); m._AVAIL.clear()
        rc, calls, out, _ = self.run_with({"codex": ["context window exceeded: prompt is too long"]})
        self.assertEqual(([c[1] for c in calls], rc), (["codex", "claude"], 0))
        self.assertNotIn("codex", m.BROKEN); self.assertIn("codex", m.available())

    def test_agent_that_gives_up_hands_over_to_the_next_provider(self):
        rc, calls, out, _ = self.run_with({"codex": [{"status": "blocked", "questions": ["which API version?"]}]})
        self.assertEqual([c[1] for c in calls], ["codex", "claude"]); self.assertEqual(rc, 0, out)
        self.assertIn("a previous codex attempt ended blocked (which API version?)", calls[1][3])

    def test_failed_agent_escalates_one_tier(self):
        rc, calls, out, r = self.run_with({"codex": [{"status": "failed"}, {"status": "no_change"}], "claude": [{"status": "failed"}]})
        self.assertEqual([(c[1], c[2]) for c in calls], [("codex", 1), ("claude", 1), ("codex", 2)]); self.assertEqual(rc, 0, out)
        self.assertTrue(any("T1→T2 [FAILED_T1]" in e for e in r.escalations))

    def test_blocked_only_when_no_provider_can_continue(self):
        rc, calls, out, _ = self.run_with({"codex": ["usage limit reached"], "claude": ["usage limit reached"]})
        self.assertEqual(rc, 1); self.assertIn("BLOCKED ·", out)
        self.assertIn("no working provider for implementer (tried codex, claude)", out); self.assertIn("rate-limited: claude until", out)

    def test_blocked_when_the_agent_needs_information(self):
        q = {"status": "blocked", "questions": ["Which payment provider should be used?"]}
        rc, calls, out, r = self.run_with({"codex": [q], "claude": [q]})
        self.assertEqual((rc, len(calls), r.escalations), (1, 2, []))           # both providers asked; no bigger model can know it
        self.assertIn("BLOCKED ·", out); self.assertIn("the agent needs information: Which payment provider", out)


class NativeExecution(Base):
    """Each CLI runs with its own tools and its own safety model; ai-team only picks the role's permissions."""

    def command(self, provider, write, role="implementer", research=False, tier=1):
        captured = {}

        def fake_run(cmd, **k):
            captured["cmd"], captured["cwd"] = cmd, k.get("cwd")
            if provider == "codex":
                Path(cmd[cmd.index("-o") + 1]).write_text('done\n{"status":"done"}')
                return subprocess.CompletedProcess(cmd, 0, '{"usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}', "")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"result": "ok", "usage": {}}), "")
        with patch.object(m.subprocess, "run", side_effect=fake_run):
            REAL_RUN_PROVIDER(provider, tier, "the task", write, role, Path(tempfile.mkdtemp()), "t", cwd="/repo", research=research)
        m.MODEL_CALLS[0] -= 1  # the command was captured, not executed
        self.assertEqual(captured["cwd"], "/repo")
        return captured["cmd"]

    def test_native_capabilities_are_preserved(self):
        for provider in ("codex", "claude"):
            for role, write in (("implementer", True), ("answer", False), ("researcher", False)):
                c = self.command(provider, write, role)
                for flag in ("--disable", "--strict-mcp-config", "--disable-slash-commands", "--tools",
                             "--setting-sources", "--system-prompt", "--settings"):
                    self.assertNotIn(flag, c)
                self.assertFalse(any("enabled=false" in x for x in c))
        self.assertNotIn("-s", self.command("codex", True))
        self.assertNotIn("--permission-mode", self.command("claude", True))

    def test_explicit_reader_permissions(self):
        c = self.command("codex", False, "explorer")
        self.assertEqual(c[c.index("-s") + 1], "read-only")
        c = self.command("claude", False, "explorer")
        self.assertEqual(c[c.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("WebSearch", c)
        self.assertIn("Edit", c[c.index("--disallowedTools") + 1:])


class UniversalRouter(Base):
    def test_general_review_is_not_a_clean_tree_shortcut(self):
        p = plan("review my travel itinerary")
        self.assertFalse(p.get("local"))
        self.assertFalse(p["repository_task"])
        self.assertNotIn("git diff", m.task_prompt("reviewer", "review my itinerary"))

    def test_nonrepo_tasks_skip_repository_verification(self):
        for task in ("fix my Bluetooth connection", "configure my IDE settings", "create a travel itinerary"):
            calls = stub({"implementer": "Completed the requested task."})
            with patch.object(m, "snapshot", side_effect=AssertionError("repo snapshot")), \
                 patch.object(m, "check_commands", side_effect=AssertionError("repo checks")), \
                 patch.object(m, "generated_dirs", side_effect=AssertionError("repo outputs")):
                rc, out, run = execute(task)
            self.assertEqual(rc, 0, out)
            self.assertEqual(len(calls), 1)
            self.assertIn("Completed the requested task.", out)

    def test_non_git_directory_routes(self):
        with tempfile.TemporaryDirectory() as d:
            p = plan("research laptop troubleshooting", root=Path(d))
            self.assertFalse(p.get("local"))
            self.assertTrue(p["steps"])


class SingleAgent(Base):
    def test_one_call_does_the_whole_change(self):
        calls = stub({"implementer": lambda t: (NO_AUTH / "src/cart.js").write_text("module.exports = 2\n") and {"status": "done", "summary": "edited"}})
        rc, out, _ = execute("add loading state to submit button")
        self.assertEqual([c[0] for c in calls], ["implementer"]); self.assertEqual(rc, 0, out)
        self.assertTrue(calls[0][4]); self.assertTrue(calls[0][3].startswith("add loading state to submit button"))
        self.assertIn("Summary:     edited", out); self.assertIn("Changed:     src/cart.js", out)

    def test_unconfirmed_no_change_is_not_done(self):
        calls = stub({"implementer": "I could not find a submit button in this project."})
        rc, out, r = execute("add loading state to submit button")
        self.assertEqual((len(calls), rc, r.escalations), (1, 1, []))             # no extra spend, but no false DONE
        self.assertIn("NOT DONE", out); self.assertIn("I could not find a submit button", out)

    def test_read_only_answer_is_the_output(self):
        calls = stub({"explorer": "The router lives in bin/ai-team.\n{\"status\":\"done\",\"summary\":\"found\"}"})
        rc, out, _ = execute("where is the router configured?")
        self.assertEqual([(c[0], c[4]) for c in calls], [("explorer", False)]); self.assertEqual(rc, 0, out)
        self.assertIn("The router lives in bin/ai-team.", out)

    def test_read_only_failure_escalates_one_tier(self):
        calls = stub({"explorer": lambda t: {"status": "failed"} if t == 1 else {"status": "done", "summary": "ok"}})
        rc, out, r = execute("explain what this component does")
        self.assertEqual([c[2] for c in calls], [1, 1, 2]); self.assertEqual(rc, 0, out)

    def test_review_task_uses_the_agents_own_git_tools(self):
        (NO_AUTH / "src/cart.js").write_text("module.exports = 2\n")
        calls = stub({"reviewer": {"status": "done", "issues": [{"severity": "low", "file": "src/cart.js", "line": 1, "problem": "p", "fix": "f"}]}})
        rc, out, _ = execute("review my current changes")
        self.assertEqual([c[0] for c in calls], ["reviewer"]); self.assertEqual(rc, 0, out)
        self.assertIn("git diff", calls[0][3]); self.assertIn("[low] src/cart.js:1 p → f", out)


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
        execute("add loading state to submit button", rmap={"commands": CMDS})
        self.assertEqual(called, [])

    def test_checks_the_task_is_about_are_strict(self):
        with contextlib.redirect_stdout(io.StringIO()):
            fix_all = m.Run("fix all current TypeScript errors", plan("fix all current TypeScript errors"), NO_AUTH, {"commands": CMDS})
            ordinary = m.Run("add loading state to submit button", plan("add loading state to submit button"), NO_AUTH, {"commands": CMDS})
            tests = m.Run("fix the failing tests", plan("fix the failing tests"), NO_AUTH, {"commands": CMDS})
        self.assertEqual(fix_all.strict, {"tc", "lint", "test", "build"}); self.assertIsNone(fix_all.baseline("tc"))
        self.assertEqual(ordinary.strict, set())
        self.assertEqual(tests.strict, {"test"})


class CheckCommands(Base):
    def test_mutating_checks_are_never_run_by_ai_team(self):
        root = git_repo({"package.json": {"scripts": {"lint": "eslint . --fix", "format": "yarn run prettier --write", "prettier": "prettier src",
                                                      "typecheck": "tsc", "test": "vitest run", "build": "vite build", "dev": "vite"}}})
        rmap = {"commands": {"lint": "npm run lint", "format": "yarn format", "typecheck": "npm run typecheck", "test": "npm test",
                             "build": "npm run build", "dev": "npm run dev"}}
        self.assertEqual(m.check_commands(root, rmap), {"test": "npm test", "build": "npm run build"})   # dev servers never

    def test_read_only_commands_are_kept(self):
        root = git_repo({"package.json": {"scripts": {"check": "yarn run lint:ci", "lint:ci": "eslint ."}}})
        rmap = {"commands": {"typecheck": "yarn tsc --noEmit", "lint": "yarn check", "test": "pytest -q"}}
        self.assertEqual(m.check_commands(root, rmap), rmap["commands"])
        self.assertTrue(m.mutating("jest -u", {})); self.assertTrue(m.mutating("tsc -b", {})); self.assertFalse(m.mutating("tsc -p x --noEmit", {}))


class Escalation(Base):
    def test_bounded_retries_with_reason_codes(self):
        def edit(tier):
            (NO_AUTH / "src/cart.js").write_text(f"module.exports = {tier}{len(calls)}\n")
            return {"status": "done"}
        calls = stub({"implementer": edit})
        m.verify = lambda *a: (False, [{"cmd": "tc", "ok": False, "tail": "src/cart.js(1,1): error TS2304: Cannot find name 'x'."}])
        rc, out, r = execute("add loading state to submit button", rmap={"commands": CMDS})
        self.assertEqual([c[2] for c in calls], [1, 1, 2])   # T1 ×2 → [FAILED_T1] → T2; same failure a 3rd time → stop
        self.assertTrue(any("[FAILED_T1]" in e for e in r.escalations))
        self.assertTrue(any("[REPEATED_FAILURE]" in e for e in r.escalations))
        self.assertEqual(rc, 1)
        self.assertIn("PREVIOUS ATTEMPT FAILED", calls[1][3])                    # the failure goes back to the agent


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


class Budgets(Base):
    def test_modes_differ(self):
        t = "implement job filter on the jobs page"
        econ, bal, qual = plan(t, "economy"), plan(t, "balanced"), plan(t, "quality")
        self.assertEqual((roles(econ), econ["steps"][0]["tier"]), (["implementer"], 1))   # cheapest: one tier lower, no review
        self.assertEqual((roles(bal, "conditional"), bal["steps"][0]["tier"]), (["reviewer"], 2))
        self.assertEqual(roles(qual, "required"), ["implementer", "reviewer"])

    def test_no_t3_at_plan_time(self):
        for budget in ("economy", "balanced", "quality"):
            for e in EVAL:
                self.assertFalse(any(s["tier"] == 3 for s in plan(e[1], budget)["steps"]), (budget, e[1]))


class RiskReview(Base):
    def needs(self, rel, body, budget="balanced", task="implement job filter on the jobs page", root=None):
        d = root or git_repo({rel: "x = 1\n"})
        (d / rel).write_text(body)
        with contextlib.redirect_stdout(io.StringIO()):
            return m.Run(task, plan(task, budget, d), d, {"commands": {}}).needs_review([rel])

    def test_ordinary_changes_are_not_reviewed(self):
        for rel, body in (("src/util.ts", "x = 2\n"), ("README.md", "".join(f"line {i}\n" for i in range(200))), ("src/a.css", ".a{}\n"),
                          ("src/util.ts", "".join(f"y{i} = {i}\n" for i in range(80)))):
            with self.subTest(rel=rel):
                self.assertFalse(self.needs(rel, body))

    def test_security_sensitive_or_critical_changes_are_reviewed(self):
        self.assertTrue(self.needs("src/auth/session.ts", "x = 2\n"))
        self.assertTrue(self.needs("src/util.ts", "const password = hash(x)\n"))
        self.assertTrue(self.needs("src/auth/session.ts", "export const session = 1\n", task="fix authentication race condition", root=WITH_AUTH))

    def test_budget_decides(self):
        self.assertFalse(self.needs("src/auth/session.ts", "x = 2\n", "economy"))
        self.assertTrue(self.needs("src/util.ts", "x = 2\n", "quality"))

    def test_blocking_issue_gets_one_fix_round(self):
        root = git_repo({"src/auth/session.ts": "export const ttl = 60\n"})
        seen = []

        def impl(tier):
            seen.append(tier)
            (root / "src/auth/session.ts").write_text("export const ttl = 3600\n" if len(seen) == 1 else "export const ttl = 600\n")
            return {"status": "done", "summary": "ttl"}
        calls = stub({"implementer": impl, "reviewer": {"status": "done", "issues": [{"severity": "high", "file": "src/auth/session.ts", "line": 1,
                                                                                       "problem": "session too long", "fix": "use 600"}]}})
        rc, out, _ = execute("change the session ttl", root=root)
        self.assertEqual([c[0] for c in calls], ["implementer", "reviewer", "implementer"]); self.assertEqual(rc, 0, out)
        self.assertNotEqual(calls[0][1], calls[1][1])                             # cross-provider review
        self.assertIn("Review:      1 blocking issue(s) fixed", out)


class ReviewerWording(Base):
    def reviewer(self, task, budget="balanced"):
        return next(s for s in plan(task, budget)["steps"] if s["role"] == "reviewer")

    def test_cross_provider(self):
        r = self.reviewer("find and fix an authentication race condition")
        self.assertIn("cross-provider review", r["why"])
        self.assertIn("the other provider", r["why"]); self.assertNotIn("only available provider", r["why"])
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
            return {"status": "done"}
        calls = stub({"implementer": impl})
        m.verify = REAL_VERIFY
        rc, out, r = execute("add loading state to submit button", root=repo, rmap={"commands": {"typecheck": check}})
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
        self.assertGreaterEqual(len(calls), 2)                          # retried / escalated
        self.assertTrue(any("[FAILED_T1]" in e or "REPEATED_FAILURE" in e for e in r.escalations))
        self.assertEqual((repo / "src/dirty.js").read_text(), "user's own uncommitted work\n")


class GeneratedOutput(Base):
    def repo(self):
        return git_repo({".gitignore": "dist-*\n", "src/app.css": ".a{colr:red}\n", "build/gen.js": "module.exports = 1\n",
                         "vite.config.ts": "export default { build: { outDir: `dist-${mode}` } }\n"},
                        after={"dist-dev/assets/index.css": ".b{-webkit-box:1}\n"})

    def test_generated_dirs_need_evidence(self):
        root = self.repo()
        self.assertEqual(m.generated_dirs(root), {"dist-dev": "git-ignored, output in vite.config.ts"})   # tracked build/ is source
        mp = m.detect_map(root)
        self.assertIn("dist-dev", mp["generated_dirs"]); self.assertNotIn("dist-dev", mp["dirs"])

    def test_agent_edits_to_generated_output_are_reverted(self):
        root = self.repo()

        def impl(tier):
            (root / "dist-dev/assets/index.css").write_text(".b{display:flex}\n")   # the agent also "fixes" build output
            (root / "dist-dev/assets/new.css").write_text("x\n")
            (root / "src/app.css").write_text(".a{color:red}\n")
            return {"status": "done", "summary": "fixed css"}
        calls = stub({"implementer": impl})
        rc, out, _ = execute("fix the css color property", root=root)
        self.assertEqual(rc, 0, out)
        self.assertEqual((root / "dist-dev/assets/index.css").read_text(), ".b{-webkit-box:1}\n")
        self.assertFalse((root / "dist-dev/assets/new.css").exists())
        self.assertEqual((root / "src/app.css").read_text(), ".a{color:red}\n")      # the source fix stays
        self.assertIn("Never edit generated output (dist-dev/)", calls[0][3])
        self.assertIn("implementer edited generated output", out); self.assertIn("generated-file edit(s) reverted", out)


class ContextEstimate(Base):
    def test_scopes(self):
        self.assertEqual(plan("fix the bug in src/cart.js")["scope"], "files")
        self.assertEqual(plan("rename foo to bar across the codebase")["scope"], "repo")
        self.assertEqual(plan("add loading state to submit button")["scope"], "module")
        self.assertEqual(plan("what is a race condition?")["scope"], "none")

    def test_long_context_is_not_a_job_for_the_cheapest_tier(self):
        task = "rename foo to bar across the codebase"
        self.assertEqual(plan(task)["steps"][0]["tier"], 1)
        with patch.dict(m.LIM, {"long_context_tokens": 10}):
            p = plan(task)
            self.assertEqual((p["steps"][0]["tier"], p.get("long_context")), (2, True))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                m.print_plan(p, NO_AUTH, {}, task)
            self.assertIn("long context: T2+", buf.getvalue())


class Security(Base):
    def test_redaction(self):
        for secret in ("sk-ant-api03-" + "a" * 30, "AKIA" + "A" * 16, "ghp_" + "b" * 36, "Bearer " + "c" * 40, "password=hunter22xyz",
                       "-----BEGIN " + "RSA PRIVATE KEY-----\nabcdefghij\n-----END " + "RSA PRIVATE KEY-----", "AIza" + "d" * 35):
            with self.subTest(secret=secret[:12]):
                self.assertNotIn(secret.split()[-1][-10:], m.redact(f"log {secret} end"))

    def test_state_files_are_redacted(self):
        stub({"implementer": {"status": "no_change"}})
        task = "set api_key=sk-" + "z" * 30
        with contextlib.redirect_stdout(io.StringIO()):
            r = m.Run(task, plan("add loading state to submit button"), NO_AUTH, {"commands": {}})
            r.execute()
        for f in r.dir.iterdir():
            self.assertNotIn("z" * 30, f.read_text(), f.name)

    def test_state_retention(self):
        root = git_repo({"a.txt": "x\n"})
        d = root / ".ai" / "state" / "tasks"
        for i in range(m.LIM["retain_tasks"] + 5):
            (d / f"T2026010{i:02d}").mkdir(parents=True)
        m.prune_state(root)
        self.assertEqual(len(list(d.iterdir())), m.LIM["retain_tasks"])


if __name__ == "__main__":
    unittest.main()
