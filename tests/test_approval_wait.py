from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from approval_wait import WaitStore, chat_notice, main, normalize_orca, probe_orca, validate


def event(second=1, **values):
    return dict(version=1, scope="orca.test-host", dispatch_id="dispatch-test",
                source="synthetic.test", observed_at=f"2026-01-01T00:00:{second:02d}+00:00",
                **dict({"state": "pending", "kind": "approval", "request_id": "request-1",
                        "revision": "payload-v1"}, **values))


def snapshot(**observation):
    return {"ok": True, "result": {"dispatch": {"id": "dispatch-test"},
                                  "observation": observation}}


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.path = self.root / "private" / "waits.sqlite3"
        self.store = WaitStore(self.path, self.project)
        self.addCleanup(self.store.close)

    def test_persist_until_ack_then_suppress_same_request(self):
        first = self.store.observe(event())
        with_store = WaitStore(self.path, self.project)
        try:
            self.assertEqual(with_store.pending()[0]["notice"], first["notice"])
            again = with_store.observe(event(2))
            self.assertEqual(again["notice"], first["notice"])
            with_store.ack(first["notice"]["id"])
        finally:
            with_store.close()
        result = self.store.observe(event(3))
        self.assertEqual(result["action"], "suppressed")
        self.assertIsNone(result["notice"])
        self.assertEqual(result["permission_decision"], "unknown")

    def test_new_request_and_changed_revision_are_not_suppressed(self):
        a = self.store.observe(event())["notice"]["id"]
        self.store.ack(a)
        b = self.store.observe(event(2, request_id="request-2"))["notice"]["id"]
        c = self.store.observe(event(3, request_id="request-2", revision="changed"))["notice"]["id"]
        self.assertEqual(len({a, b, c}), 3)
        with self.assertRaises(ValueError):self.store.ack(a)

    def test_unidentified_pending_never_deduped_by_text_or_dispatch(self):
        a = self.store.observe(event(request_id=None, revision=None))
        self.store.ack(a["notice"]["id"])
        b = self.store.observe(event(2, request_id=None, revision=None))
        self.assertIsNotNone(b["notice"])
        self.assertEqual(b["notice"]["reason"], "pending_identity_unavailable")

    def test_unknown_does_not_close_wait_or_reannounce_it_as_current(self):
        a = self.store.observe(event())
        result = self.store.observe(event(2, state="unknown", request_id=None, revision=None))
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["action"], "inspect_unknown")
        self.assertEqual(result["notice"], a["notice"])
        self.assertEqual(self.store.pending()[0]["latest_observation"], "unknown")
        replay = self.store.observe(event(2, state="unknown", request_id=None, revision=None))
        self.assertEqual(replay["state"], "unknown")
        self.store.ack(a["notice"]["id"])
        self.assertEqual(self.store.observe(event(3))["action"], "suppressed")

    def test_clear_emits_resolution_without_inventing_approval(self):
        old = self.store.observe(event())["notice"]["id"]
        clear = self.store.observe(event(2, state="clear", request_id=None, revision=None))
        self.assertEqual(clear["notice"]["reason"], "wait_no_longer_observed")
        self.assertEqual(clear["permission_decision"], "unknown")
        with self.assertRaises(ValueError):self.store.ack(old)
        self.store.ack(clear["notice"]["id"])
        self.assertEqual(self.store.observe(event(3, state="clear", request_id=None, revision=None))["action"], "suppressed")
        self.assertIsNotNone(self.store.observe(event(4))["notice"])

    def test_stale_and_conflicting_snapshots_do_not_reopen(self):
        self.store.observe(event(3, state="clear", request_id=None, revision=None))
        for second in (1, 3):
            result = self.store.observe(event(second))
            self.assertEqual(result["action"], "stale_or_conflicting_observation")
            self.assertEqual(result["state"], "clear")
        self.assertEqual(self.store.pending(), [])

    def test_ended_dispatch_does_not_restart_on_late_wait(self):
        result = self.store.observe(event(state="ended", request_id=None, revision=None))
        self.assertIsNone(result["notice"])
        result = self.store.observe(event(2))
        self.assertEqual(result["action"], "inspect_conflicting_lifecycle")
        self.assertEqual(result["state"], "ended")

    def test_exact_replay_and_separate_scope(self):
        a = self.store.observe(event())
        self.assertEqual(self.store.observe(event())["notice"], a["notice"])
        self.store.ack(a["notice"]["id"])
        self.assertIsNone(self.store.observe(event())["notice"])
        other = event();other["scope"] = "orca.another-host"
        self.assertIsNotNone(self.store.observe(other)["notice"])

    def test_concurrent_observations_create_one_notice(self):
        def update(_):
            store = WaitStore(self.path, self.project)
            try:return store.observe(event())["notice"]["id"]
            finally:store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(update, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.store.pending()), 1)

    def test_private_state_has_no_request_payload_or_identity(self):
        self.store.observe(event(request_id="PRIVATE_REQUEST_ID", revision="PRIVATE_COMMAND_CONTENT"))
        self.assertNotIn(b"PRIVATE_REQUEST_ID", self.path.read_bytes())
        self.assertNotIn(b"PRIVATE_COMMAND_CONTENT", self.path.read_bytes())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        for output in [self.project / "db", self.root / "public" / "db"]:
            if output.parent != self.project:output.parent.mkdir(mode=0o755)
            with self.assertRaises(ValueError):WaitStore(output, self.project)
        link = self.root / "alias";link.symlink_to(self.path.parent)
        with self.assertRaises(ValueError):WaitStore(link / "db", self.project)

    def test_rejects_foreign_database(self):
        path = self.path.parent / "foreign.sqlite3"
        with sqlite3.connect(path) as db:db.execute("CREATE TABLE other(value TEXT)")
        os.chmod(path, 0o600)
        with self.assertRaises(ValueError):WaitStore(path, self.project)
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [("other",)])

    def test_input_validation(self):
        for change in [{"version": True}, {"observed_at": "2026-01-01"}, {"kind": "allow"},
                       {"state": "clear"}, {"request_id": ""}, {"revision": None}, {"extra": "ignored?"}]:
            value = event();value.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):validate(value)

    def test_cli_observe_ack_and_pending(self):
        value = self.root / "event.json";value.write_text(json.dumps(event()))
        base = [sys.executable, str(ROOT / "scripts/approval_wait.py"), "--project", str(self.project),
                "--state-db", str(self.path)]
        def call(*args):
            result = subprocess.run(base + list(args), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        first = call("observe", str(value))
        self.assertEqual(call("pending")["pending"][0]["notice"], first["notice"])
        call("ack", first["notice"]["id"])
        self.assertEqual(call("pending")["pending"], [])
        self.assertIsNone(call("observe", str(value))["notice"])
        # Native snapshot input requires its actual capture time, not import time.
        native = self.root / "native.json";native.write_text(json.dumps(snapshot(exactWorker=True, agentWait=None)))
        failed = subprocess.run(base + ["orca", "--scope", "orca.test", "--dispatch", "dispatch-test", "--snapshot", str(native)], capture_output=True, text=True, timeout=10)
        self.assertEqual(failed.returncode, 2)

    def test_chat_preparation_does_not_ack_and_retry_keeps_notice(self):
        a = self.store.observe(event())
        chat = chat_notice(a, live=True)
        self.assertTrue(chat['deliver'])
        self.assertEqual(chat['notice_id'], a['notice']['id'])
        self.assertEqual(len(self.store.pending()), 1)
        replay = chat_notice(self.store.observe(event()), live=True)
        self.assertEqual(replay['notice_id'], chat['notice_id'])
        self.store.ack(chat['notice_id'])
        self.assertFalse(chat_notice(self.store.observe(event(2)), live=True)['deliver'])

    def test_chat_does_not_present_history_or_unknown_as_current_wait(self):
        a = self.store.observe(event())
        self.assertFalse(chat_notice(a, live=False)['deliver'])
        unknown = self.store.observe(event(2, state='unknown', request_id=None, revision=None))
        rendered = chat_notice(unknown, live=True)
        self.assertFalse(rendered['deliver'])
        self.assertIsNone(rendered['text'])
        stale = self.store.observe(event())
        self.assertFalse(chat_notice(stale, live=True)['deliver'])
        self.assertEqual(self.store.pending()[0]['notice'], a['notice'])

    def test_chat_resolution_is_not_permission_decision(self):
        for state in ('clear', 'ended'):
            with self.subTest(state=state):
                # Begin a wait before the terminal resolution on a separate dispatch.
                value = event(1)
                value['dispatch_id'] = 'dispatch-' + state
                self.store.observe(value)
                value.update(observed_at='2026-01-01T00:00:02+00:00', state=state, request_id=None, revision=None)
                chat = chat_notice(self.store.observe(value), live=True)
                self.assertTrue(chat['deliver'])
                self.assertEqual(chat['permission_decision'], 'unknown')
                self.assertIn('승인 또는 거부 결과', chat['text'])

    def test_native_chat_never_leaks_prompt_or_invents_source_identity(self):
        source = snapshot(status='live', exactWorker=True, agentWait={
            'source':'hook', 'reason':'agent-interactive-prompt',
            'requestId':'unverified-id', 'prompt':'SECRET_COMMAND_CONTENT'})
        value = normalize_orca(source, 'orca.test', 'dispatch-test', '2026-01-01T00:00:01Z')
        self.assertIsNone(value['request_id'])
        a = self.store.observe(value)
        chat = chat_notice(a, live=True)
        self.assertIn('원본 요청 ID가 없어', chat['text'])
        self.assertNotIn('SECRET_COMMAND_CONTENT', json.dumps(chat))
        self.store.ack(chat['notice_id'])
        value['observed_at']='2026-01-01T00:00:02Z'
        self.assertTrue(chat_notice(self.store.observe(value), live=True)['deliver'])

    def test_chat_cli_is_one_probe_without_delivery_or_ack(self):
        args=['--project',str(self.project),'--state-db',str(self.path),'--chat',
              'orca','--scope','orca.test','--dispatch','dispatch-test','--live','--orca-cli','/chosen/orca']
        output=io.StringIO()
        with patch.dict(os.environ, {'ORCA_ENVIRONMENT':'','ORCA_PAIRING_CODE':''}), \
                patch('approval_wait.probe_orca', return_value=event()) as probe, redirect_stdout(output):
            self.assertEqual(main(args),0)
        self.assertEqual(probe.call_count,1)
        self.assertTrue(json.loads(output.getvalue())['chat']['deliver'])
        self.assertEqual(len(self.store.pending()),1)
        source=self.root/'snapshot.json'
        source.write_text(json.dumps(snapshot(status='live',exactWorker=True,agentWait={'source':'hook'})))
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(args[:-3]+['--snapshot',str(source),'--observed-at','2026-01-01T00:00:03Z']),0)
        self.assertFalse(json.loads(output.getvalue())['chat']['deliver'])


class NativeTests(unittest.TestCase):
    def normalize(self, value):
        return normalize_orca(value, "orca.test", "dispatch-test", "2026-01-01T00:00:00Z")

    def test_absent_null_pending_and_exact_exit(self):
        for observation, state in [({}, "unknown"), ({"exactWorker": True}, "unknown"),
                ({"status":"live", "exactWorker": True, "agentWait": None}, "clear"),
                ({"status":"live", "exactWorker": True, "agentWait": {"evidence": "synthetic-only"}}, "pending"),
                ({"exactWorker": True, "agentWait": None}, "unknown"),
                ({"status":"unverifiable", "exactWorker": True, "agentWait": None}, "unknown"),
                ({"status":"unverifiable", "exactWorker": True, "agentWait": {"source":"hook"}}, "unknown"),
                ({"exactWorker": True, "status": "exited"}, "ended"),
                ({"status": "exited", "exactWorker": False}, "unknown"),
                ({"exactWorker": True, "status": "exited", "agentWait": {"evidence": "conflicting"}}, "unknown"),
                ({"exactWorker": True, "agentWait": "unexpected"}, "unknown")]:
            with self.subTest(observation=observation):
                value = self.normalize(snapshot(**observation))
                self.assertEqual(value["state"], state)
                self.assertIsNone(value["request_id"])

    def test_unexpected_identity_and_failed_snapshot_rejected(self):
        for value in [{"ok": False}, dict(snapshot(exactWorker=True),error={'code':'unexpected'}), {"ok": True, "result": {"dispatch": None}},
                      {"ok": True, "result": {"dispatch": {"id": "other"}}}]:
            with self.assertRaises(ValueError):self.normalize(value)

    def test_live_probe_is_one_read_only_call_with_no_fallback(self):
        response = subprocess.CompletedProcess([], 0, json.dumps(snapshot(status='live',exactWorker=True, agentWait=None)), "")
        with patch("approval_wait.subprocess.run", return_value=response) as run:
            result = probe_orca("/chosen/orca", ROOT, "orca.local", "dispatch-test")
            self.assertEqual(result["state"], "clear")
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0], ["/chosen/orca", "orchestration", "worker-show", "--dispatch", "dispatch-test", "--full", "--environment", "local", "--json"])
        with patch("approval_wait.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "SECRET")) as run:
            with self.assertRaises(ValueError):probe_orca("/chosen/orca", ROOT, "orca.local", "dispatch-test")
            self.assertEqual(run.call_count, 1)


if __name__ == "__main__":unittest.main()
