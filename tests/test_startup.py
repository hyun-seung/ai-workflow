import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import startup


def orca_status(session="session", connected=True):
    return {"ok": True, "result": {
        "runtime": {"state": "ready", "reachable": True,
                    "connectionState": "connected" if connected else "disconnected", "appVersion": "test"},
        "caller": {"live": True, "orcaSessionId": "orca_session_id:" + session}}}


def response(data, code=0):
    return subprocess.CompletedProcess([], code, json.dumps(data), "PRIVATE_ERROR_BODY")


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name).resolve()
        (self.project / "AGENTS.md").write_text("Project rules", encoding="utf-8")

    def config(self, **values):
        p = self.project / ".ai-workflow-startup.json"
        p.write_text(json.dumps({"version": 1, **values}), encoding="utf-8")
        return p

    def run_start(self, environ, result, explicit=None):
        with patch.dict("os.environ", environ, clear=True), patch.object(startup.shutil, "which", return_value="/fake/cli"), \
                patch.object(startup.subprocess, "run", return_value=result) as run:
            selected = startup.startup(self.project, explicit)
        return selected, run

    def test_orca_session_and_live_identity_select_only_orca(self):
        selected, run = self.run_start({"ORCA_AGENT_SESSION_ID": "session"}, response(orca_status()))
        self.assertEqual(selected["environment"], "orca")
        self.assertEqual(selected["selection_source"], "session")
        self.assertTrue(selected["connection"]["session_identity_confirmed"])
        self.assertFalse(selected["project_scope_confirmed"])
        self.assertTrue(selected["environment_guide"].endswith("environments/orca.md"))
        self.assertEqual(len(selected["common_guides"]), 3)
        self.assertEqual(selected["project_instructions"], [str(self.project / "AGENTS.md")])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1:], ["status", "--json"])

    def test_no_session_or_both_sessions_require_explicit_not_installed_cli(self):
        for env in ({}, {"ORCA_CLI_COMMAND": "/fake/orca", "PASEO_HOST": "example:1234"},
                    {"ORCA_AGENT_SESSION_ID": "a", "PASEO_AGENT_ID": "b"}):
            with self.subTest(env=env), patch.dict("os.environ", env, clear=True), \
                    patch.object(startup.subprocess, "run") as run:
                with self.assertRaisesRegex(startup.StartupError, "missing or ambiguous"):
                    startup.startup(self.project)
                run.assert_not_called()

    def test_explicit_argument_over_config_and_session_with_no_fallback(self):
        self.config(environment="paseo")
        selected, run = self.run_start({"PASEO_AGENT_ID": "p"}, response(orca_status()), "orca")
        self.assertEqual(selected["environment"], "orca")
        self.assertEqual(selected["selection_source"], "argument")
        self.assertFalse(selected["connection"]["session_identity_confirmed"])
        self.assertEqual(run.call_count, 1)

    def test_explicit_auto_overrides_config_and_fails_without_session(self):
        self.config(environment="orca")
        with patch.dict("os.environ", {}, clear=True), patch.object(startup.subprocess, "run") as run:
            with self.assertRaises(startup.StartupError):
                startup.startup(self.project, "auto")
            run.assert_not_called()

    def test_explicit_config_and_provider_are_independent(self):
        self.config(environment="paseo", agent={"provider": "codex", "model": "example-model"})
        selected, _ = self.run_start({}, response({"connectedDaemon": "reachable", "serverId": "example", "daemonVersion": "test"}))
        self.assertEqual(selected["environment"], "paseo")
        self.assertEqual(selected["selection_source"], "config")
        self.assertEqual(selected["agent_request"]["provider"], "codex")
        self.assertFalse(selected["connection"]["session_identity_confirmed"])

    def test_paseo_session_does_not_claim_live_agent_confirmed(self):
        selected, run = self.run_start({"PASEO_AGENT_ID": "example"}, response({"connectedDaemon": "reachable", "serverId": "example"}))
        self.assertEqual(selected["environment"], "paseo")
        self.assertEqual(selected["selection_source"], "session")
        self.assertFalse(selected["connection"]["session_identity_confirmed"])
        self.assertEqual(run.call_args.args[0], ["paseo", "status", "--json"])

    def test_failed_probe_does_not_probe_other_environment_or_leak_body(self):
        for result in (response({"error": "PRIVATE"}, 1), response({"error": "PRIVATE"}),
                       response(orca_status(connected=False)), response(orca_status("wrong"))):
            with self.subTest(result=result), patch.dict("os.environ", {"ORCA_AGENT_SESSION_ID": "session"}, clear=True), \
                    patch.object(startup.shutil, "which", return_value="/fake/orca"), \
                    patch.object(startup.subprocess, "run", return_value=result) as run:
                with self.assertRaises(startup.StartupError) as raised:
                    startup.startup(self.project)
                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertEqual(run.call_count, 1)

    def test_missing_cli_and_status_timeout_do_not_fallback(self):
        with patch.dict("os.environ", {"ORCA_AGENT_SESSION_ID": "session"}, clear=True), \
                patch.object(startup.shutil, "which", return_value=None), patch.object(startup.subprocess, "run") as run:
            with self.assertRaisesRegex(startup.StartupError, "CLI unavailable"):
                startup.startup(self.project)
            run.assert_not_called()
        with patch.dict("os.environ", {"ORCA_AGENT_SESSION_ID": "session"}, clear=True), \
                patch.object(startup.shutil, "which", return_value="/fake/orca"), \
                patch.object(startup.subprocess, "run", side_effect=subprocess.TimeoutExpired("orca", 1)) as run:
            with self.assertRaises(startup.StartupError):
                startup.startup(self.project)
            self.assertEqual(run.call_count, 1)

    def test_malformed_and_duplicate_status_json_are_errors(self):
        for text in ("broken", "[]", '{"ok":true,"ok":false}',
                     '{"connectedDaemon":"reachable","serverId":"example","extra":NaN}'):
            with self.subTest(text=text), patch.object(startup.subprocess, "run",
                    return_value=subprocess.CompletedProcess([], 0, text, "")):
                with self.assertRaises(startup.StartupError):
                    startup.command_json(["fake", "status", "--json"], self.project, 1)

    def test_parent_and_two_repository_guides(self):
        for name in ("backend", "mobile"):
            (self.project / name).mkdir()
            (self.project / name / "AGENTS.md").write_text(name)
        self.config(repositories={name: {"path": name} for name in ("backend", "mobile")})
        loaded = startup.load_project(self.project)
        self.assertEqual(set(loaded["repositories"]), {"backend", "mobile"})
        for name in ("backend", "mobile"):
            self.assertEqual(loaded["repositories"][name]["project_instructions"],
                             [str(self.project / name / "AGENTS.md")])

    def test_repository_and_instruction_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other).resolve()
            (outside / "external.md").write_text("private")
            (self.project / "link.md").symlink_to(outside / "external.md")
            (self.project / "linked-repo").symlink_to(outside)
            for config in ({"project_instructions": ["../missing.md"]},
                           {"project_instructions": ["link.md"]},
                           {"repositories": {"external": {"path": "linked-repo"}}}):
                with self.subTest(config=config):
                    self.config(**config)
                    with self.assertRaises(startup.StartupError):
                        startup.load_project(self.project)

    def test_declared_missing_guides_duplicate_repository_or_unknown_keys_fail(self):
        for config in ({"project_instructions": ["missing.md"]},
                       {"repositories": {"same": {"path": "."}}},
                       {"repositories": {"child": {"path": ".", "unknown": True}}},
                       {"environment": "codex"}, {"agent": {"provider": "codex", "environment": "orca"}},
                       {"unknown": True}, {"version": True}):
            with self.subTest(config=config):
                self.config(**config)
                with self.assertRaises(startup.StartupError):
                    startup.load_project(self.project)
        (self.project / "child").mkdir()
        self.config(repositories={"a": {"path": "child"}, "b": {"path": "child"}})
        with self.assertRaises(startup.StartupError):
            startup.load_project(self.project)

    def test_explicit_missing_symlink_duplicate_and_nonfinite_configs_fail(self):
        with self.assertRaises(startup.StartupError):
            startup.load_project(self.project, self.project / ".ai-workflow-startup.json")
        actual = self.config(environment="orca")
        link = self.project / "alias.json"
        link.symlink_to(actual)
        with self.assertRaises(startup.StartupError):
            startup.load_project(self.project, link)
        for text in ('{"version":1,"version":1}', '{"version":1,"agent":{"model":NaN}}'):
            actual.write_text(text)
            with self.assertRaises(startup.StartupError):
                startup.load_project(self.project)

    def test_orca_binary_selection_is_single_and_linux_avoids_screen_reader(self):
        with patch.object(startup.shutil, "which", return_value="/fake"):
            self.assertEqual(startup.executable_for("orca", {"ORCA_CLI_COMMAND": "/one/orca"}), "/one/orca")
            self.assertEqual(startup.executable_for("orca", {"ORCA_DEV_REPO_ROOT": "/dev"}), "orca-dev")
            with patch.object(startup.sys, "platform", "linux"):
                self.assertEqual(startup.executable_for("orca", {}), "orca-ide")

    def test_cli_errors_and_timeout_validation_start_no_models(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), patch.object(startup.subprocess, "run") as run:
                with self.assertRaises(startup.StartupError):
                    startup.startup(self.project, "orca", timeout=timeout)
                run.assert_not_called()
        err = io.StringIO()
        with patch.dict("os.environ", {}, clear=True), patch.object(startup.subprocess, "run") as run, \
                contextlib.redirect_stderr(err):
            self.assertEqual(startup.main(["--project", str(self.project)]), 2)
            run.assert_not_called()
        self.assertIn("missing or ambiguous", err.getvalue())


if __name__ == "__main__":
    unittest.main()
