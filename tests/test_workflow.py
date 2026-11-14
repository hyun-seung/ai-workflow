import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("workflow", ROOT / "scripts/workflow.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.template = self.root / "template"
        self.project = self.root / "project with spaces"
        self.local = self.root / "private"
        for path in (self.template, self.project, self.local):
            path.mkdir()
        (self.template / "common.md").write_text("COMMON_POLICY", encoding="utf-8")
        (self.template / "role.md").write_text("ROLE_POLICY", encoding="utf-8")
        self.base = {
            "version": 1, "adapter": "codex", "instructions": ["common.md"],
            "roles": {"coordinator": {
                "model": "gpt-6.1-sol", "effort": "low", "sandbox": "read-only",
                "instructions": ["role.md"],
            }},
        }
        self.write(self.template / "workflow.json", self.base)

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_repository_defaults_load_all_six_roles(self):
        config = workflow.load_config(ROOT, self.project)
        self.assertEqual(len(config["roles"]), 6)
        for name in config["roles"]:
            command, prompt, _, _ = workflow.prepare_run(config, self.project, name, "Example task")
            self.assertIn("Example task", prompt)
            self.assertIn("--ephemeral", command)

    def test_layer_precedence_and_relative_instruction_origins(self):
        (self.project / "rules.md").write_text("PROJECT_POLICY", encoding="utf-8")
        (self.local / "role.md").write_text("LOCAL_ROLE_POLICY", encoding="utf-8")
        self.write(self.project / ".ai-workflow.json", {
            "version": 1, "instructions": ["rules.md"],
            "roles": {"coordinator": {"effort": "medium"}},
        })
        local = self.local / "override.json"
        self.write(local, {
            "version": 1,
            "roles": {"coordinator": {"model": "gpt-6-astra", "instructions": ["role.md"]}},
        })
        config = workflow.load_config(self.template, self.project, local)
        command, prompt, paths, _ = workflow.prepare_run(config, self.project, "coordinator", "작업")
        self.assertEqual(config["roles"]["coordinator"]["effort"], "medium")
        self.assertIn("gpt-6-astra", command)
        for policy in ("COMMON_POLICY", "PROJECT_POLICY", "LOCAL_ROLE_POLICY"):
            self.assertIn(policy, prompt)
        self.assertNotIn(self.template / "role.md", paths)
        self.assertEqual(workflow.load_config(self.template, self.project)["roles"]["coordinator"]["model"], "gpt-6.1-sol")

    def test_rejects_typos_fake_budget_fields_and_unknown_roles(self):
        for override in (
            {"token_budget": 1000},
            {"roles": {"coordintor": {"effort": "medium"}}},
            {"roles": {"coordinator": {"efort": "medium"}}},
            {"roles": {"coordinator": {"sandbox": "danger-full-access"}}},
            {"roles": {"coordinator": {"effort": ["high"]}}},
            {"roles": {"coordinator": {"model": "$(bad command)"}}},
            {"roles": {"coordinator": {"instructions": []}}},
            {"adapter": "not-implemented"},
            {"version": True},
        ):
            with self.subTest(override=override):
                self.write(self.project / ".ai-workflow.json", {"version": 1, **override})
                with self.assertRaises(workflow.ConfigError):
                    workflow.load_config(self.template, self.project)

    def test_rejects_missing_or_escaping_instruction_files(self):
        outside = self.root / "outside.md"
        outside.write_text("PRIVATE_CONTENT", encoding="utf-8")
        (self.project / "linked.md").symlink_to(outside)
        for path in ("missing.md", "../outside.md", str(outside), "linked.md"):
            with self.subTest(path=path):
                self.write(self.project / ".ai-workflow.json", {"version": 1, "instructions": [path]})
                with self.assertRaises(workflow.ConfigError):
                    workflow.load_config(self.template, self.project)

    def test_project_config_symlink_requires_explicit_local_config(self):
        local = self.local / "override.json"
        self.write(local, {"version": 1})
        (self.project / ".ai-workflow.json").symlink_to(local)
        with self.assertRaises(workflow.ConfigError):
            workflow.load_config(self.template, self.project)

    def test_duplicate_json_keys_do_not_silently_override(self):
        (self.project / ".ai-workflow.json").write_text('{"version":1,"version":2}', encoding="utf-8")
        with self.assertRaises(workflow.ConfigError):
            workflow.load_config(self.template, self.project)

    def test_invalid_request_does_not_launch_model(self):
        for arguments in (
            ["run", "--role", "unknown", "--task", "task"],
            ["run", "--role", "coordinator", "--task", "  "],
            ["check", "--role", "unknown"],
        ):
            with self.subTest(arguments=arguments), patch.object(workflow, "TEMPLATE_ROOT", self.template), \
                 patch.object(workflow, "execute_run") as run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(workflow.main(arguments + ["--project", str(self.project)]), 2)
                run.assert_not_called()

    def test_dry_run_never_starts_codex_or_prints_prompt_contents(self):
        output = io.StringIO()
        with patch.object(workflow, "TEMPLATE_ROOT", self.template), \
             patch.object(workflow, "execute_run") as run, contextlib.redirect_stdout(output):
            code = workflow.main([
                "run", "--project", str(self.project), "--role", "coordinator",
                "--task", "PRIVATE_TASK_MARKER", "--dry-run",
            ])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertNotIn("PRIVATE_TASK_MARKER", output.getvalue())
        self.assertNotIn("COMMON_POLICY", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["roles"]["coordinator"]["model"], "gpt-6.1-sol")

    def test_fake_codex_receives_settings_prompt_cwd_and_exit_code_is_preserved(self):
        binary_dir = self.root / "bin"
        binary_dir.mkdir()
        capture = self.root / "capture.json"
        fake = binary_dir / "codex"
        fake.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if '--version' in sys.argv:\n    print('codex-cli 0.167.0'); sys.exit(0)\n"
            f"Path({str(capture)!r}).write_text(json.dumps({{'args':sys.argv[1:], 'stdin':sys.stdin.read(), 'cwd':os.getcwd()}}))\n"
            "sys.exit(7)\n", encoding="utf-8",
        )
        fake.chmod(0o700)
        marker = self.root / "must-not-exist"
        task = f'작업 $(touch "{marker}")'
        with patch.object(workflow, "TEMPLATE_ROOT", self.template), \
             patch.dict(os.environ, {"PATH": str(binary_dir) + os.pathsep + os.environ.get("PATH", "")}), \
             patch.object(sys, "stdin", io.StringIO(task)):
            code = workflow.main(["run", "--project", str(self.project), "--role", "coordinator", "--task", "-"])
        self.assertEqual(code, 7)
        recorded = json.loads(capture.read_text())
        self.assertEqual(recorded["cwd"], str(self.project))
        self.assertIn('model_reasoning_effort="low"', recorded["args"])
        self.assertIn("read-only", recorded["args"])
        self.assertIn("gpt-6.1-sol", recorded["args"])
        self.assertIn("COMMON_POLICY", recorded["stdin"])
        self.assertIn("ROLE_POLICY", recorded["stdin"])
        self.assertIn(f"자료 기준 경로: {self.template / 'role.md'}", recorded["stdin"])
        self.assertIn(task, recorded["stdin"])
        self.assertNotIn(task, recorded["args"])
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
