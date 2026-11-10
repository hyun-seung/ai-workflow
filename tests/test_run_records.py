import contextlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import codex_runner
import workflow
from run_records import JsonRecordStore, UsageCollector, validate_records_dir


def completed(**usage):
    return {"type": "turn.completed", "usage": usage}


class UsageTests(unittest.TestCase):
    def test_duplicate_terminal_and_subsets_are_not_added_twice(self):
        collector = UsageCollector()
        collector.consume({"type": "turn.started"})
        event = completed(input_tokens=100, output_tokens=20, cached_input_tokens=75, reasoning_output_tokens=5)
        collector.consume(event)
        collector.consume(event)
        result = collector.result("succeeded")
        self.assertEqual(result["total_tokens"], 120)
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["cached_input_tokens"], 75)

    def test_missing_and_measured_zero_are_distinct(self):
        collector = UsageCollector()
        self.assertEqual(collector.result("failed")["status"], "unavailable")
        self.assertIsNone(collector.result("failed")["total_tokens"])
        collector.consume(completed(input_tokens=0, output_tokens=0))
        result = collector.result("succeeded")
        self.assertEqual(result["status"], "measured")
        self.assertEqual(result["total_tokens"], 0)
        self.assertIsNone(result["cached_input_tokens"])
        self.assertIsNone(result["reasoning_output_tokens"])

    def test_bad_values_and_missing_primary_counts_are_partial(self):
        for bad in (True, -1, 1.5, "10", None):
            with self.subTest(bad=bad):
                collector = UsageCollector()
                collector.consume(completed(input_tokens=bad, output_tokens=5))
                result = collector.result("succeeded")
                self.assertEqual(result["status"], "partial")
                self.assertIsNone(result["input_tokens"])
                self.assertIsNone(result["total_tokens"])

    def test_impossible_subsets_are_discarded(self):
        collector = UsageCollector()
        collector.consume(completed(input_tokens=10, output_tokens=5, cached_input_tokens=11, reasoning_output_tokens=6))
        result = collector.result("succeeded")
        self.assertEqual(result["total_tokens"], 15)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["cached_input_tokens"])
        self.assertIsNone(result["reasoning_output_tokens"])

    def test_multiple_turns_and_conflicting_events_are_not_summed(self):
        collector = UsageCollector()
        for count in (100, 200):
            collector.consume({"type": "turn.started"})
            collector.consume(completed(input_tokens=count, output_tokens=20))
        result = collector.result("succeeded")
        self.assertEqual(result["total_tokens"], 220)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["scope"], "last_reported_turn")

    def test_failed_interrupted_and_timed_out_usage_is_not_complete(self):
        collector = UsageCollector()
        collector.consume(completed(input_tokens=100, output_tokens=20))
        for status in ("failed", "interrupted", "timed_out"):
            self.assertEqual(collector.result(status)["status"], "partial")
        collector.consume({"type": "turn.failed", "error": {"message": "PRIVATE_ERROR"}})
        result = collector.result("failed")
        self.assertIn("failed_turn", result["issues"])
        self.assertNotIn("PRIVATE_ERROR", json.dumps(result))


class RecordIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.records = self.root / "private" / "runs"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", "")}

    def fake_codex(self, events=(), code=0, extra="", prelude=""):
        body = (
            f"#!{sys.executable}\n"
            "import json, os, signal, subprocess, sys, time\nfrom pathlib import Path\n"
            "if '--version' in sys.argv:\n    print('codex-cli 0.167.0'); sys.exit(0)\n"
            + prelude + "\n"
            + "prompt = sys.stdin.read()\n"
            + extra + "\n"
            + f"events = {events!r}\n"
            + "for event in events:\n    print(event if isinstance(event, str) else json.dumps(event), flush=True)\n"
            + f"sys.exit({code})\n"
        )
        executable = self.bin / "codex"
        executable.write_text(body, encoding="utf-8")
        executable.chmod(0o700)

    def args(self, record=True):
        args = ["run", "--project", str(self.project), "--role", "coordinator",
                "--task-id", "TASK-01", "--task", "PRIVATE_PROMPT"]
        if record:
            args += ["--records-dir", str(self.records)]
        return args

    def invoke(self, args=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = workflow.main(args or self.args())
        return code, out.getvalue(), err.getvalue()

    def record(self):
        files = list(self.records.glob("*.json"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text(encoding="utf-8"))

    def test_subprocess_records_usage_and_linkage_without_private_payloads(self):
        self.fake_codex([
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "aggregated_output": "PRIVATE_CODE"}},
            {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "aggregated_output": "PRIVATE_CODE"}},
            {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "PRIVATE_ANSWER"}},
            completed(input_tokens=100, output_tokens=20, cached_input_tokens=60),
        ], extra=f"assert any(json.loads(p.read_text())['execution_status'] == 'running' for p in Path({str(self.records)!r}).glob('*.json'))")
        code, out, err = self.invoke(self.args() + ["--result-ref", "reports/TASK-01.md", "--parent-task-id", "WORK-01", "--task-type", "development"])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "PRIVATE_ANSWER\n")
        record = self.record()
        self.assertEqual(record["execution_status"], "succeeded")
        self.assertEqual(record["task_verification"], "not_evaluated")
        self.assertEqual(record["usage"]["total_tokens"], 120)
        self.assertEqual(record["runtime_version"], "codex-cli 0.167.0")
        self.assertEqual(record["task_id"], "TASK-01")
        self.assertEqual(record["parent_task_id"], "WORK-01")
        self.assertEqual(record["task_type"], "development")
        self.assertEqual(record["observed"], {"model": None, "effort": None})
        self.assertEqual(record["result_refs"], ["reports/TASK-01.md"])
        self.assertIsNotNone(record["finished_at"])
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertEqual(record["diagnostics"]["events"]["completed_tool_items"], 1)
        self.assertEqual(record["diagnostics"]["events"]["status"], "observed")
        self.assertGreater(record["diagnostics"]["prompt"]["prompt_characters"], 0)
        for value in ("PRIVATE_PROMPT", "PRIVATE_CODE", "PRIVATE_ANSWER", str(self.project)):
            self.assertNotIn(value, json.dumps(record))
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.records.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(next(self.records.glob("*.json")).stat().st_mode), 0o600)
        self.assertEqual(len(list(self.records.iterdir())), 1)

    def test_collection_without_storage_creates_no_records(self):
        self.fake_codex([completed(input_tokens=10, output_tokens=5)])
        code, _, err = self.invoke(self.args(record=False))
        self.assertEqual(code, 0)
        self.assertIn("total=15", err)
        self.assertFalse(self.records.exists())

    def test_retry_is_a_new_run_under_the_same_task(self):
        self.fake_codex([completed(input_tokens=10, output_tokens=5)])
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.invoke()[0], 0)
        records = [json.loads(p.read_text()) for p in self.records.glob("*.json")]
        self.assertEqual(len({r["run_id"] for r in records}), 2)
        self.assertEqual({r["task_id"] for r in records}, {"TASK-01"})

    def test_failure_and_missing_usage_are_not_zero_or_success(self):
        self.fake_codex([{"type": "turn.failed", "error": {"message": "PRIVATE_ERROR"}}], code=7)
        self.assertEqual(self.invoke()[0], 7)
        record = self.record()
        self.assertEqual(record["execution_status"], "failed")
        self.assertEqual(record["usage"]["status"], "unavailable")
        self.assertIsNone(record["usage"]["input_tokens"])
        self.assertNotIn("PRIVATE_ERROR", json.dumps(record))

    def test_zero_exit_without_completed_event_is_failure(self):
        self.fake_codex([{"type": "turn.started"}])
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.record()["failure_reason"], "missing_success_event")

    def test_new_unfinished_turn_does_not_reuse_earlier_success(self):
        self.fake_codex([{"type": "turn.started"}, completed(input_tokens=10, output_tokens=5), {"type": "turn.started"}])
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.record()["usage"]["status"], "partial")

    def test_success_without_usage_is_unavailable(self):
        self.fake_codex([{"type": "turn.completed"}])
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.record()["usage"]["status"], "unavailable")

    def test_malformed_and_oversized_events_do_not_hide_usage(self):
        self.fake_codex(["PRIVATE_MALFORMED", completed(input_tokens=100, output_tokens=20)],
                        prelude="print('x' * (8 * 1024 * 1024 + 10), flush=True)")
        code, out, err = self.invoke()
        self.assertEqual(code, 0, err)
        result = self.record()["usage"]
        self.assertEqual(result["status"], "partial")
        self.assertIn("invalid_event", result["issues"])
        self.assertIn("oversized_event", result["issues"])
        self.assertNotIn("PRIVATE_MALFORMED", out + err + json.dumps(result))

    def test_large_prompt_and_early_output_do_not_deadlock(self):
        self.fake_codex([completed(input_tokens=10, output_tokens=5)],
                        prelude="print(json.dumps({'type':'item.completed','item':{'type':'command_execution','output':'x'*200000}}), flush=True)",
                        extra="assert len(prompt) > 200000")
        args = self.args()
        args[args.index("--task") + 1] = "-"
        result = subprocess.run([sys.executable, str(ROOT / "scripts/workflow.py"), *args],
                                env=self.env, input="PRIVATE_PROMPT" * 20000, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.record()["usage"]["total_tokens"], 15)

    def test_dry_run_never_creates_storage_or_launches(self):
        with patch.object(codex_runner.subprocess, "Popen") as popen:
            code, out, _ = self.invoke(self.args() + ["--dry-run"])
        self.assertEqual(code, 0)
        popen.assert_not_called()
        self.assertFalse(self.records.exists())
        self.assertNotIn("PRIVATE_PROMPT", out)

    def test_invalid_linkage_timeout_and_reference_rejected_before_launch(self):
        cases = [["--parent-task-id", "TASK-01"], ["--parent-task-id", "../private"],
                 ["--result-ref", " "], ["--timeout-seconds", "0"], ["--timeout-seconds", "-1"],
                 ["--timeout-seconds", "nan"], ["--timeout-seconds", "inf"]]
        for args in cases:
            with self.subTest(args=args), patch.object(codex_runner.subprocess, "Popen") as popen:
                self.assertEqual(self.invoke(self.args() + args)[0], 2)
                popen.assert_not_called()
        self.assertFalse(self.records.exists())

    def test_project_git_and_symlink_destinations_are_rejected(self):
        git = self.root / "other-repo"
        git.mkdir()
        (git / ".git").write_text("gitdir: /example/worktree")
        linked = self.root / "linked"
        linked.symlink_to(self.project, target_is_directory=True)
        for directory in (self.project / "runs", ROOT / "runs", git / "runs", linked / "runs"):
            with self.subTest(directory=directory), self.assertRaises(ValueError):
                validate_records_dir(directory, [ROOT, self.project])

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_shared_storage_is_rejected_without_changing_permissions(self):
        self.records.mkdir(parents=True, mode=0o755)
        self.records.chmod(0o755)
        with self.assertRaises(ValueError):
            JsonRecordStore(self.records, [ROOT, self.project])
        self.assertEqual(stat.S_IMODE(self.records.stat().st_mode), 0o755)

    def test_initial_storage_failure_prevents_launch(self):
        self.fake_codex()
        with patch.object(JsonRecordStore, "write", side_effect=OSError("write failed")), \
             patch.object(codex_runner.subprocess, "Popen") as popen:
            self.assertEqual(self.invoke()[0], 2)
            popen.assert_not_called()

    def test_final_storage_failure_keeps_initial_record_and_reports_error(self):
        self.fake_codex([completed(input_tokens=10, output_tokens=5)])
        original = JsonRecordStore.write

        def fail_final(store, record):
            if record["execution_status"] != "running":
                raise OSError("disk full")
            return original(store, record)

        with patch.object(JsonRecordStore, "write", fail_final):
            code, _, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertIn("final record could not be saved", err)
        self.assertEqual(self.record()["execution_status"], "running")

    def test_launch_failure_has_no_exception_payload_in_record(self):
        self.fake_codex()
        with patch.object(codex_runner, "codex_version", return_value=None), \
             patch.object(codex_runner.subprocess, "Popen", side_effect=OSError("PRIVATE_EXCEPTION")):
            code, _, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(self.record()["failure_reason"], "launch_failed")
        self.assertNotIn("PRIVATE_EXCEPTION", err + json.dumps(self.record()))

    def test_signal_handlers_are_restored(self):
        self.fake_codex([completed(input_tokens=10, output_tokens=5)])
        before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(before, {s: signal.getsignal(s) for s in before})

    def test_timeout_during_version_check_prevents_model_launch(self):
        self.fake_codex()
        with patch.object(codex_runner, "codex_version", side_effect=lambda: time.sleep(0.02)), \
             patch.object(codex_runner.subprocess, "Popen") as popen:
            code, _, _ = self.invoke(self.args() + ["--timeout-seconds", "0.001"])
        self.assertEqual(code, 124)
        popen.assert_not_called()
        self.assertEqual(self.record()["execution_status"], "timed_out")

    def check_stop(self, signum=None, timeout=False, descendants=False):
        ready = self.root / "ready"
        child_ready = self.root / "child-ready"
        extra = ""
        if descendants:
            child_code = ("import os, signal, time\nfrom pathlib import Path\n"
                          "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                          f"Path({str(child_ready)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
            extra += f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
            extra += f"while not Path({str(child_ready)!r}).exists():\n    time.sleep(0.01)\n"
        extra += f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        if timeout:
            extra += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        extra += "time.sleep(60)"
        self.fake_codex(extra=extra)
        args = self.args() + (["--timeout-seconds", "1"] if timeout else [])
        process = subprocess.Popen([sys.executable, str(ROOT / "scripts/workflow.py"), *args],
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists())
            if signum:
                process.send_signal(signum)
            _, err = process.communicate(timeout=8)
            expected = 124 if timeout else 128 + signum
            self.assertEqual(process.returncode, expected, err)
            record = self.record()
            self.assertEqual(record["execution_status"], "timed_out" if timeout else "interrupted")
            self.assertEqual(record["failure_reason"], "timeout" if timeout else "sigint" if signum == signal.SIGINT else "sigterm")
            self.assertEqual(record["usage"]["status"], "unavailable")
            self.assertIsNotNone(record["finished_at"])
            self.assertIsNotNone(record["exit_code"])
            # The 60-second child and grandchild inherit this pipe's stderr.
            # communicate() receiving EOF within 8 seconds proves they closed it;
            # a surviving grandchild would keep this call blocked, even after parent exit.
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
            # Clean up only the process group created by this test if an assertion failed.
            if ready.exists():
                try:
                    os.killpg(int(ready.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_sigint_finalizes_record(self):
        self.check_stop(signal.SIGINT)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_sigterm_stops_descendants_and_finalizes_record(self):
        self.check_stop(signal.SIGTERM, descendants=True)

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_timeout_kills_unresponsive_process(self):
        self.check_stop(timeout=True)


if __name__ == "__main__":
    unittest.main()
