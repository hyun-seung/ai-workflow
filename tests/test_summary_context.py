import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import workflow
from context_input import load_context
from run_diagnostics import EventDiagnostics
from run_records import new_record
from run_summary import summarize


def record(status="measured", cached=60):
    value = new_record("verifier", {"model": "example-model", "effort": "medium", "sandbox": "read-only"}, "TASK-01", parent_task_id="WORK-01")
    value.update(execution_status="succeeded", exit_code=0, duration_ms=1000)
    value["usage"].update(status=status, scope="single_turn", input_tokens=100, output_tokens=20,
                          total_tokens=120, cached_input_tokens=cached, issues=[])
    return value


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def write(self, filename, data):
        (self.root / filename).write_text(json.dumps(data), encoding="utf-8")

    def test_duplicate_run_counts_once_and_retries_count_separately(self):
        first = record()
        self.write("one.json", first)
        self.write("copy.json", first)
        self.write("retry.json", record())
        result = summarize(self.root)
        self.assertEqual(result["totals"]["runs"], 2)
        self.assertEqual(result["totals"]["measured_total_tokens"], 240)
        self.assertEqual(result["scan"]["duplicate_files"], 1)

    def test_conflicting_run_excluded_instead_of_picking_arbitrarily(self):
        first = record()
        self.write("one.json", first)
        first["duration_ms"] = 2000
        self.write("conflict.json", first)
        result = summarize(self.root)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["scan"]["conflicting_run_ids"], 1)
        self.assertEqual(result["totals"]["runs"], 0)
        self.assertIsNone(result["totals"]["measured_total_tokens"])

    def test_partial_unavailable_and_missing_cache_not_treated_as_zero(self):
        self.write("measured.json", record(cached=None))
        self.write("partial.json", record(status="partial"))
        absent = new_record("verifier", {"model": "example-model", "effort": "medium", "sandbox": "read-only"})
        self.write("running.json", absent)
        result = summarize(self.root)["totals"]
        self.assertEqual(result["runs"], 3)
        self.assertEqual(result["measured_total_tokens"], 120)
        self.assertEqual(result["usage_counts"], {"measured": 1, "partial": 1, "unavailable": 1})
        self.assertIsNone(result["cached_input_tokens"])
        self.assertEqual(result["runs_with_cache_details"], 0)
        self.assertIsNone(result["observed_completed_tool_items"])

    def test_cache_ratio_uses_only_measured_runs_with_cache_details(self):
        self.write("known.json", record())
        self.write("unknown.json", record(cached=None))
        self.write("partial.json", record(status="partial", cached=99))
        result = summarize(self.root)["totals"]
        self.assertEqual(result["measured_total_tokens"], 240)
        self.assertEqual(result["cache_ratio_percent"], 60)
        self.assertEqual(result["uncached_input_tokens_with_cache_details"], 40)

    def test_filters_and_model_groups_are_separate(self):
        self.write("one.json", record())
        other = record()
        other["requested"]["effort"] = "high"
        self.write("two.json", other)
        self.assertEqual(len(summarize(self.root)["groups"]), 2)
        self.assertEqual(summarize(self.root, parent_task_id="WORK-01")["totals"]["runs"], 2)
        self.assertEqual(summarize(self.root, role="backend")["totals"]["runs"], 0)

    def test_invalid_files_symlinks_and_unknown_versions_are_visible(self):
        (self.root / "broken.json").write_text("PRIVATE_BAD_JSON")
        malformed = record()
        malformed["usage"]["total_tokens"] = 999
        self.write("bad-total.json", malformed)
        future = record()
        future["schema_version"] = 999
        self.write("future.json", future)
        (self.root / "linked.json").symlink_to(self.root / "future.json")
        result = summarize(self.root)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["scan"]["invalid_files"], 2)
        self.assertEqual(result["scan"]["unsupported_versions"], 1)
        self.assertEqual(result["scan"]["ignored_symlinks"], 1)
        self.assertNotIn("PRIVATE_BAD_JSON", json.dumps(result))

    def test_legacy_record_without_diagnostics_remains_readable(self):
        old = record()
        old.pop("diagnostics")
        self.write("old.json", old)
        result = summarize(self.root)
        self.assertTrue(result["scan_complete"])
        self.assertIsNone(result["totals"]["prompt_characters_min"])

    def test_duplicate_json_keys_are_rejected(self):
        data = json.dumps(record())
        (self.root / "ambiguous.json").write_text(data[:-1] + ', "schema_version": 1}')
        result = summarize(self.root)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["scan"]["invalid_files"], 1)

    def test_summary_cli_never_calls_a_model_or_writes_files(self):
        self.write("one.json", record())
        before = (self.root / "one.json").read_bytes()
        output = io.StringIO()
        with patch.object(workflow, "execute_run") as run, contextlib.redirect_stdout(output):
            code = workflow.main(["summary", "--records-dir", str(self.root), "--json"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["totals"]["measured_total_tokens"], 120)
        self.assertEqual(before, (self.root / "one.json").read_bytes())
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def mixed_records(self):
        for index, status in enumerate(("running", "succeeded", "failed", "interrupted", "timed_out")):
            value = record(status="measured" if status == "succeeded" else "partial")
            value["execution_status"] = status
            value["duration_ms"] = 1000 + index
            value["requested"]["model"] = f"example-{status}"
            self.write(f"{status}.json", value)

    def test_execution_status_selects_totals_and_groups_and_combines_with_ids(self):
        self.mixed_records()
        baseline = summarize(self.root)
        self.assertEqual(baseline, summarize(self.root, execution_status=None))
        self.assertEqual(baseline["totals"]["runs"], 5)
        self.assertEqual(len(baseline["groups"]), 5)
        self.assertEqual(baseline["totals"]["measured_total_tokens"], 120)
        for index, status in enumerate(("running", "succeeded", "failed", "interrupted", "timed_out")):
            filters = dict(task_id="TASK-01", parent_task_id="WORK-01", role="verifier", execution_status=status)
            with self.subTest(status=status):
                result = summarize(self.root, **filters)
                self.assertEqual(result["totals"]["runs"], 1)
                self.assertEqual(result["totals"]["duration_ms"], 1000 + index)
                self.assertEqual(result["totals"]["execution_counts"],
                                 {key: int(key == status) for key in baseline["totals"]["execution_counts"]})
                self.assertEqual(len(result["groups"]), 1)
                group = result["groups"][0]
                self.assertEqual(group["requested_model"], f"example-{status}")
                for key, value in result["totals"].items():
                    self.assertEqual(group[key], value)
                self.assertEqual(result["totals"]["measured_total_tokens"], 120 if status == "succeeded" else None)
                for key, mismatch in (("task_id", "OTHER"), ("parent_task_id", "OTHER"), ("role", "backend")):
                    empty = summarize(self.root, **{**filters, key: mismatch})
                    self.assertEqual(empty["totals"]["runs"], 0)
                    self.assertEqual(empty["groups"], [])
                    for field in ("measured_input_tokens", "measured_output_tokens", "measured_total_tokens"):
                        self.assertIsNone(empty["totals"][field])

    def test_status_filter_preserves_full_scan_and_excludes_conflicts_in_both_orders(self):
        for selected_first in (True, False):
            with self.subTest(selected_first=selected_first), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                selected = record()
                other = json.loads(json.dumps(selected))
                other["execution_status"] = "failed"
                other["usage"]["status"] = "partial"
                pair = (selected, other) if selected_first else (other, selected)
                for name, value in zip(("a.json", "b.json"), pair):
                    (directory / name).write_text(json.dumps(value))
                (directory / "copy.json").write_text(json.dumps(other))
                (directory / "invalid.json").write_text("{broken")
                future = record()
                future["schema_version"] = 999
                (directory / "future.json").write_text(json.dumps(future))
                (directory / "link.json").symlink_to(directory / "future.json")
                expected = summarize(directory)
                output = io.StringIO()
                with patch.object(workflow, "execute_run") as run, contextlib.redirect_stdout(output):
                    code = workflow.main(["summary", "--records-dir", folder, "--execution-status", "succeeded", "--json"])
                run.assert_not_called()
                self.assertEqual(code, 2)
                result = json.loads(output.getvalue())
                self.assertEqual(result, expected)
                self.assertFalse(result["scan_complete"])
                self.assertEqual(result["scan"], {"json_files": 6, "invalid_files": 1, "unsupported_versions": 1,
                                                  "ignored_symlinks": 1, "duplicate_files": int(not selected_first),
                                                  "conflicting_run_ids": 1})
                self.assertEqual(result["totals"]["runs"], 0)
                self.assertEqual(result["groups"], [])

    def test_unmatched_execution_status_has_empty_groups_and_unmeasured_totals(self):
        self.write("succeeded.json", record())
        result = summarize(self.root, execution_status="failed")
        self.assertTrue(result["scan_complete"])
        self.assertEqual(result["totals"]["runs"], 0)
        self.assertEqual(result["groups"], [])
        for field in ("measured_input_tokens", "measured_output_tokens", "measured_total_tokens"):
            self.assertIsNone(result["totals"][field])

    def test_status_cli_outputs_are_filtered_and_read_only(self):
        self.mixed_records()
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        for status in ("running", "succeeded", "failed", "interrupted", "timed_out"):
            for as_json in (False, True):
                with self.subTest(status=status, as_json=as_json):
                    output = io.StringIO()
                    args = ["summary", "--records-dir", str(self.root), "--execution-status", status]
                    with patch.object(workflow, "execute_run") as run, contextlib.redirect_stdout(output):
                        code = workflow.main(args + (["--json"] if as_json else []))
                    run.assert_not_called()
                    self.assertEqual(code, 0)
                    if as_json:
                        self.assertEqual(json.loads(output.getvalue()), summarize(self.root, execution_status=status))
                    else:
                        self.assertIn("실행 1건", output.getvalue())
                        self.assertIn(f"example-{status} / medium | 1 |", output.getvalue())
                        measured = "입력 100 / 출력 20 / 전체 120" if status == "succeeded" else "입력 미측정 / 출력 미측정 / 전체 미측정"
                        self.assertIn(f"측정 완료 실행 합계: {measured}", output.getvalue())
                        for other in ("running", "succeeded", "failed", "interrupted", "timed_out"):
                            if other != status:
                                self.assertNotIn(f"example-{other}", output.getvalue())
        self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()})

    def test_invalid_execution_status_cli_exits_two_before_scanning_or_model_call(self):
        for status in ("unknown", "SUCCEEDED", "succeeded,failed", ""):
            with self.subTest(status=status), patch.object(workflow, "execute_run") as run, \
                    patch.object(workflow, "summarize") as scan, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    workflow.main(["summary", "--records-dir", str(self.root), "--execution-status", status])
                self.assertEqual(raised.exception.code, 2)
                run.assert_not_called()
                scan.assert_not_called()

    def test_invalid_execution_status_direct_call_rejected_before_directory_access_or_aggregation(self):
        for status in ("unknown", "SUCCEEDED", "succeeded,failed", "", True, False, 0, 1, 1.5,
                       [], {}, (), b"succeeded"):
            with self.subTest(status=status), patch("run_summary.Path") as directory, \
                    patch("run_summary.add") as aggregate:
                with self.assertRaisesRegex(ValueError, "Invalid execution status filter"):
                    summarize(self.root / "missing", execution_status=status)
                directory.assert_not_called()
                aggregate.assert_not_called()


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "source.py").write_text("FIRST_PRIVATE\nSECOND_PRIVATE\nTHIRD_PRIVATE\n", encoding="utf-8")

    def test_selected_range_is_included_once_and_counts_are_not_tokens(self):
        text, stats = load_context(self.project, ["source.py:2:2", "source.py:2:2"])
        self.assertIn("2: SECOND_PRIVATE", text)
        self.assertNotIn("FIRST_PRIVATE", text)
        self.assertNotIn("THIRD_PRIVATE", text)
        self.assertEqual(stats["inline_context_sources"], 1)
        self.assertEqual(stats["inline_context_characters"], len("SECOND_PRIVATE\n"))

    def test_escaping_symlink_absolute_and_invalid_ranges_rejected(self):
        outside = self.root / "outside.txt"
        outside.write_text("PRIVATE_OUTSIDE")
        (self.project / "linked.txt").symlink_to(outside)
        for spec in ("../outside.txt", str(outside), "linked.txt", "source.py:3:2", "source.py:1:10", "missing.txt"):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                load_context(self.project, [spec])

    def test_binary_oversized_and_combined_limit_rejected(self):
        (self.project / "binary.txt").write_bytes(b"\x00x")
        (self.project / "invalid.txt").write_bytes(b"\xff")
        (self.project / "empty.txt").write_bytes(b"")
        (self.project / "large.txt").write_bytes(b"x" * 65537)
        (self.project / "one.txt").write_text("x" * 40000)
        (self.project / "two.txt").write_text("y" * 40000)
        for specs in (["binary.txt"], ["invalid.txt"], ["empty.txt"], ["large.txt"], ["one.txt", "two.txt"]):
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                load_context(self.project, specs)

    def test_dry_run_hides_context_but_keeps_all_policy_documents(self):
        config = workflow.load_config(ROOT, self.project)
        _, prompt, paths, stats = workflow.prepare_run(config, self.project, "verifier", "Example", ["source.py:2:2"])
        self.assertIn("SECOND_PRIVATE", prompt)
        self.assertIn(ROOT / "principles/privacy.md", paths)
        self.assertEqual(stats["prompt_characters"], len(prompt))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(workflow, "execute_run") as run:
            code = workflow.main(["run", "--project", str(self.project), "--role", "verifier", "--task", "Example",
                                  "--context-file", "source.py:2:2", "--dry-run"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertNotIn("SECOND_PRIVATE", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["prompt_diagnostics"]["inline_context_sources"], 1)


class DiagnosticsTests(unittest.TestCase):
    def test_completed_items_deduplicate_without_guessing_model_call_count(self):
        stats = EventDiagnostics()
        stats.consume("item_1", "command_execution")
        stats.consume("item_1", "command_execution")
        stats.consume("item_2", "agent_message")
        result = stats.result(complete=True)
        self.assertEqual(result["completed_tool_items"], 1)
        self.assertEqual(result["status"], "observed")
        self.assertIsNone(result["model_calls"])
        self.assertNotIn("item_1", json.dumps(result))

    def test_missing_ids_and_unknown_categories_are_explicit(self):
        stats = EventDiagnostics()
        stats.consume(None, "PRIVATE_TOOL_TYPE")
        result = stats.result(complete=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["items_without_id"], 1)
        self.assertEqual(result["completed_items"]["other"], 1)
        self.assertNotIn("PRIVATE_TOOL_TYPE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
