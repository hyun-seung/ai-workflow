from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
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
from approval_wait import WaitStore, chat_notice, main, normalize_orca, probe_orca, validate, normalize_approval, read_approval_json, instant


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
        with closing(sqlite3.connect(path)) as db:db.execute("CREATE TABLE other(value TEXT)")
        os.chmod(path, 0o600)
        with self.assertRaises(ValueError):WaitStore(path, self.project)
        with closing(sqlite3.connect(path)) as db:
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


def approval(second=1, state="open", by="person", **changes):
    """Synthetic data shaped like installed WorkerApprovalPayloadSchema v1."""
    payload = {"v": 1, "approvalId": "approval-1", "state": state,
               "worker": {"handle": "worker-test", "dispatchId": "dispatch-test", "taskId": "task-test"},
               "summary": {"title": "PRIVATE_COMMAND", "detail": "PRIVATE_DETAIL", "reason": None},
               "allowedActions": ["allow", "deny"]}
    if state != "open":
        payload.update(allowedActions=[], outcome={"by": by, "decision": None,
                                                  "reason": None, "reasonDelivered": False})
    payload.update(changes)
    envelope = {"scope": "orca.test-host", "observed_at": f"2026-01-01T00:00:{second:02d}Z", "payload": payload}
    binding = {"scope": envelope["scope"], "observed_at": envelope["observed_at"],
               "dispatch_id": payload["worker"]["dispatchId"], "task_id": payload["worker"]["taskId"],
               "worker_handle": payload["worker"]["handle"], "approval_id": payload["approvalId"]}
    return envelope, binding


class ApprovalTests(unittest.TestCase):
    setUp = WaitTests.setUp
    def test_v1_repeat_restart_ack_changes_new_identity_and_reappearance(self):
        first = self.store.observe_approval(*approval())
        self.assertEqual(first["state"], "pending")
        self.assertEqual(first["source"], "orca.worker-approval.v1")
        self.assertEqual(first["current_identity"], "unknown")
        with closing(WaitStore(self.path, self.project)) as restarted:
            self.assertEqual(restarted.observe_approval(*approval(2))["notice"], first["notice"])
            restarted.ack(first["notice"]["id"])
        self.assertEqual(self.store.observe_approval(*approval(3))["action"], "suppressed")
        changed = self.store.observe_approval(*approval(4, summary={"title":"CHANGED", "detail":None, "reason":None}))
        self.assertNotEqual(changed["notice"]["id"], first["notice"]["id"])
        new = self.store.observe_approval(*approval(5, approvalId="approval-2"))
        self.assertNotEqual(new["notice"]["id"], changed["notice"]["id"])
        closed = self.store.observe_approval(*approval(6, state="answered", approvalId="approval-2"))
        self.assertEqual(closed["state"], "clear")
        reopened = self.store.observe_approval(*approval(7, approvalId="approval-2"))
        self.assertNotEqual(reopened["notice"]["id"], new["notice"]["id"])
        for result in (first, changed, new, closed, reopened):
            self.assertEqual(result["permission_decision"], "unknown")
        self.assertNotIn(b"PRIVATE_COMMAND", self.path.read_bytes())
        self.assertNotIn(b"PRIVATE_DETAIL", self.path.read_bytes())
        self.assertNotIn(b"approval-1", self.path.read_bytes())
        self.assertNotIn("PRIVATE", json.dumps(first))

    def test_v1_closed_outcomes_and_person_only(self):
        for by, state, expected in [("person","answered","clear"), ("auto","expired","clear"),
                ("worker-ended","answered","ended"), ("terminal-only","answered","unknown"),
                ("unknown","answered","clear"), ("worker-chat","answered","clear"),
                ("worker-terminal","answered","clear"), ("coordinator","answered","clear")]:
            with self.subTest(by=by):
                value, _ = normalize_approval(*approval(state=state, by=by, personOnly=True))
                self.assertEqual(value["state"], expected)
        value, _ = normalize_approval(*approval(personOnly=True, expiresAt="2026-01-01T00:01:00Z"))
        self.assertEqual(value["request_id"], "approval-1")
        self.assertEqual(value["source"], "orca.worker-approval.v1")
        self.assertEqual(normalize_approval(*approval(allowedActions=["deny","allow"]))[0]["revision"],
                         normalize_approval(*approval())[0]["revision"])

    def test_v1_identity_binding_and_rebinding_survive_restart(self):
        self.store.observe_approval(*approval())
        for key in ("scope", "observed_at", "dispatch_id", "task_id", "worker_handle", "approval_id"):
            data, binding = approval(2)
            binding[key] = "other" if key != "observed_at" else "2026-01-01T00:00:03Z"
            with self.subTest(key=key), self.assertRaises(ValueError):normalize_approval(data, binding)
        with closing(WaitStore(self.path, self.project)) as restarted:
            for key in ("handle", "dispatchId", "taskId"):
                data, binding = approval(3, worker=dict(approval()[0]["payload"]["worker"], **{key:"other"}))
                with self.subTest(key=key), self.assertRaises(ValueError):restarted.observe_approval(data, binding)
        data, binding = approval(4)
        data["scope"] = binding["scope"] = "orca.other-host"
        self.assertIsNotNone(self.store.observe_approval(data, binding)["notice"])

    def test_v1_strict_contract_and_contradictions(self):
        bad = [{"v":None}, {"v":2}, {"v":True}, {"state":None}, {"personOnly":False},
               {"allowedActions":[]}, {"allowedActions":["allow","allow"]},
               {"allowedActions":["execute"]}, {"outcome":None}, {"summary":{}}, {"unknown":"x"},
               {"expiresAt":None}, {"expiresAt":"yesterday"}]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):normalize_approval(*approval(**changes))
        for state, by, outcome_changes in [("answered","auto",{}), ("expired","person",{}),
                ("answered","person",{"reasonDelivered":True}),
                ("answered","worker-ended",{"decision":"allow"}),
                ("answered","terminal-only",{"decision":"deny"}),
                ("answered","coordinator",{"decision":"allow"}),
                ("answered","person",{"by":"invented"}), ("answered","person",{"decision":"yes"})]:
            data, binding = approval(state=state, by=by, personOnly=True)
            data["payload"]["outcome"].update(outcome_changes)
            with self.subTest(state=state, by=by, changes=outcome_changes), self.assertRaises(ValueError):normalize_approval(data, binding)
        for field in ("outcome", "summary", "worker"):
            data, binding = approval(state="answered")
            del data["payload"][field]
            with self.assertRaises(ValueError):normalize_approval(data, binding)
        for data in (None, [], {}, {"scope":"test"}):
            with self.assertRaises(ValueError):normalize_approval(data, approval()[1])

    def test_v1_ordering_old_close_and_lifecycle(self):
        first = self.store.observe_approval(*approval(3))
        self.assertEqual(self.store.observe_approval(*approval(1))["action"], "stale_or_conflicting_observation")
        self.assertEqual(self.store.observe_approval(*approval(3, personOnly=True))["action"], "stale_or_conflicting_observation")
        new = self.store.observe_approval(*approval(4, approvalId="approval-2"))
        self.assertEqual(self.store.observe_approval(*approval(5, state="answered"))["action"], "inspect_conflicting_approval")
        self.assertEqual(self.store.pending()[0]["notice"], new["notice"])
        self.store.observe_approval(*approval(6, state="answered", by="worker-ended", approvalId="approval-2"))
        self.assertEqual(self.store.observe_approval(*approval(7, approvalId="approval-2"))["action"], "inspect_conflicting_lifecycle")

    def test_v1_concurrent_atomic_ack_and_no_raw_outcome_storage(self):
        def update(_):
            with closing(WaitStore(self.path, self.project)) as store:
                return store.observe_approval(*approval())["notice"]["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(update, range(2)))
        self.assertEqual(ids[0], ids[1])
        self.store.ack(ids[0])
        self.assertIsNone(self.store.observe_approval(*approval())["notice"])
        data, binding = approval(2, state="answered")
        data["payload"]["outcome"].update(decision="allow", reason="PRIVATE_OUTCOME", reasonDelivered=True)
        result = self.store.observe_approval(data, binding)
        self.assertEqual(result["state"], "clear")
        self.assertEqual(result["permission_decision"], "unknown")
        self.assertNotIn("PRIVATE_OUTCOME", json.dumps(result))
        self.assertNotIn(b"PRIVATE_OUTCOME", self.path.read_bytes())
        self.assertFalse(chat_notice(result, live=True)["deliver"])
        before = self.store.db.execute("SELECT * FROM waits").fetchall()
        data, binding = approval(3, worker={"handle":"different", "dispatchId":"dispatch-test", "taskId":"task-test"})
        with self.assertRaises(ValueError):self.store.observe_approval(data, binding)
        self.assertEqual(before, self.store.db.execute("SELECT * FROM waits").fetchall())
        self.assertFalse(self.store.db.in_transaction)

    def test_v1_old_database_compatibility_and_terminal_only(self):
        self.store.db.execute("DROP TABLE approval_bindings")
        self.store.db.execute("DROP TABLE approval_heads")
        self.store.db.commit()
        first = self.store.observe(event())
        with closing(WaitStore(self.path, self.project)) as reopened:
            self.assertEqual(reopened.pending()[0]["notice"], first["notice"])
            reopened.observe_approval(*approval(2))
            unknown = reopened.observe_approval(*approval(3, state="answered", by="terminal-only"))
            self.assertEqual(unknown["state"], "unknown")
            self.assertEqual(unknown["action"], "inspect_unknown")
            self.assertEqual(reopened.pending()[0]["latest_observation"], "unknown")
            self.assertFalse(chat_notice(unknown, live=True)["deliver"])

    def test_v1_cli_timestamp_overflow_is_private_and_does_not_touch_db(self):
        self.store.observe(event())
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        cases = [("observed_at", "0001-01-01T00:00:00+14:00"),
                 ("expiresAt", "9999-12-31T23:59:59-14:00")]
        for field, timestamp in cases:
            data, binding = approval()
            if field == "observed_at":
                data[field] = binding[field] = timestamp
            else:
                data["payload"][field] = timestamp
            source, bound = self.root / "extreme.json", self.root / "extreme-binding.json"
            source.write_text(json.dumps(data)); bound.write_text(json.dumps(binding))
            for existing in (True, False):
                path = self.path if existing else self.root / "not-created" / "waits.sqlite3"
                with self.subTest(field=field, existing_db=existing):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "scripts/approval_wait.py"),
                         "--project", str(self.project), "--state-db", str(path),
                         "orca-approval", str(source), "--binding", str(bound)],
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(result.stderr, "Wait handling failed; check inputs or use formal access approval. State was not assumed clear.\n")
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn(timestamp, result.stderr)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.parent.iterdir()})
                    self.assertFalse((self.root / "not-created").exists())

    def test_instant_normal_boundaries_and_legacy_validation(self):
        for source, expected in [
                ("0001-01-01T00:00:00Z", "0001-01-01T00:00:00.000000+00:00"),
                ("0001-01-01T14:00:00+14:00", "0001-01-01T00:00:00.000000+00:00"),
                ("9999-12-31T23:59:59.999999Z", "9999-12-31T23:59:59.999999+00:00"),
                ("9999-12-31T09:59:59.999999-14:00", "9999-12-31T23:59:59.999999+00:00"),
                ("2026-01-01T09:00:00+09:00", "2026-01-01T00:00:00.000000+00:00")]:
            with self.subTest(source=source):self.assertEqual(instant(source), expected)
        for value in (None, 1, "invalid", "2026-01-01T00:00:00", "2026-02-30T00:00:00Z"):
            with self.subTest(value=value), self.assertRaises(ValueError):instant(value)
        for value in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as error:
                    instant(value)
                self.assertNotIn(value, str(error.exception))

    def test_v1_cli_history_no_live_and_bounded_failure_no_leak(self):
        data, binding = approval()
        source = self.root / "approval.json"
        bound = self.root / "binding.json"
        source.write_text(json.dumps(data)); bound.write_text(json.dumps(binding))
        args = ["--project", str(self.project), "--state-db", str(self.path), "--chat",
                "orca-approval", str(source), "--binding", str(bound)]
        output = io.StringIO()
        with redirect_stdout(output):self.assertEqual(main(args), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["chat"]["deliver"])
        self.assertEqual(result["chat"]["reason"], "historical_observation_not_live")
        self.assertEqual(result["evidence"], "manual_source_unverified")
        for raw in ('{"x":NaN}', '{"x":1e999}', '[' * 2000 + ']' * 2000,
                    json.dumps({"x":"a" * 65536}), '{"v":1,"v":1}'):
            source.write_text(raw)
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):read_approval_json(source)
        source.write_text('{"scope":"PRIVATE_SECRET", "payload":null}')
        with redirect_stdout(io.StringIO()), patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(main(args), 2)
            self.assertNotIn("PRIVATE_SECRET", error.getvalue())


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
