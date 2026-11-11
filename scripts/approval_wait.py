#!/usr/bin/env python3
"""One-shot wait observation and acknowledged local notices; never approve actions."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import uuid

from run_records import validate_records_dir
from startup import executable_for, reject_constant, unique_keys

ROOT = Path(__file__).resolve().parents[1]
MAX_INPUT = 1024 * 1024
STATES = {"pending", "clear", "unknown", "ended"}


def decode(text):
    return json.loads(text, object_pairs_hook=unique_keys, parse_constant=reject_constant)


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_INPUT + 1)
    if len(data) > MAX_INPUT:
        raise ValueError("Input exceeds 1 MiB")
    return decode(data.decode("utf-8"))


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value):
        raise ValueError(f"Invalid {label}")
    return value


def instant(value):
    if not isinstance(value, str):
        raise ValueError("observed_at must be an ISO timestamp with timezone")
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("observed_at must include timezone")
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds")


def validate(event):
    allowed = {"version", "scope", "dispatch_id", "observed_at", "state", "kind",
               "request_id", "revision", "source"}
    if not isinstance(event, dict) or set(event) - allowed or type(event.get("version")) is not int or event["version"] != 1:
        raise ValueError("Unsupported wait event")
    value = dict(event)
    for key in ("scope", "dispatch_id", "source"):
        identifier(value.get(key), key)
    value["observed_at"] = instant(value.get("observed_at"))
    if value.get("state") not in STATES or value.get("kind") not in {"approval", "question", "unknown"}:
        raise ValueError("Unsupported wait state or kind")
    for key in ("request_id", "revision"):
        item = value.setdefault(key, None)
        if item is not None and (not isinstance(item, str) or not item.strip() or len(item) > 500):
            raise ValueError(f"Invalid {key}")
    if value["state"] != "pending" and any(value[k] is not None for k in ("request_id", "revision")):
        raise ValueError("Only pending events can have request identity")
    if value["request_id"] is not None and value["revision"] is None:
        raise ValueError("Identified requests require a source payload revision or hash")
    return value


def normalize_orca(snapshot, scope, dispatch_id, observed_at):
    """Use only documented/observed fields; never infer identity from prompt text."""
    if (not isinstance(snapshot, dict) or snapshot.get("ok") is not True or
            snapshot.get("error") is not None):
        raise ValueError("Orca did not confirm the observation")
    result = snapshot.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("dispatch"), dict) or result["dispatch"].get("id") != dispatch_id:
        raise ValueError("Orca dispatch identity mismatch")
    observation = result.get("observation")
    state = "unknown"
    if isinstance(observation, dict) and observation.get("exactWorker") is True:
        # Task completion alone does not prove exit; conflicting evidence is unknown.
        if "agentWait" in observation and observation["agentWait"] is not None:
            if observation.get("status") == "live" and isinstance(observation["agentWait"], dict) and observation["agentWait"]:
                state = "pending"
        elif observation.get("status") == "exited":
            state = "ended"
        elif "agentWait" in observation and observation.get("status") == "live":
            state = "clear"
    return validate({"version": 1, "scope": scope, "dispatch_id": dispatch_id,
                     "observed_at": observed_at, "state": state, "kind": "unknown",
                     "source": "orca.worker-show", "request_id": None, "revision": None})


def probe_orca(cli, project, scope, dispatch_id):
    identifier(dispatch_id, "dispatch_id")
    # One read per invocation. No polling, inbox consumption, retries or permission changes.
    result = subprocess.run([cli, "orchestration", "worker-show", "--dispatch", dispatch_id,
                             "--full", "--environment", "local", "--json"], cwd=project, capture_output=True,
                            text=True, timeout=15)
    if result.returncode or len(result.stdout.encode()) > MAX_INPUT:
        raise ValueError("Orca probe denied, failed or oversized; use the formal approval path")
    observed = datetime.now(timezone.utc).isoformat()
    return normalize_orca(decode(result.stdout), scope, dispatch_id, observed)


def private_db(path, project):
    path = Path(path).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("State path must not contain symlinks")
    directory = validate_records_dir(path.parent, [ROOT, project])
    missing = []
    cursor = directory
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for item in reversed(missing):
        item.mkdir(mode=0o700, exist_ok=True)
    validate_records_dir(directory, [ROOT, project])
    path = directory / path.name
    for suffix in ("", "-journal", "-wal", "-shm"):
        item = Path(str(path) + suffix)
        if item.is_symlink():
            raise ValueError("State sidecars must not be symlinks")
        if item.exists():
            info = item.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("State files must be owned, regular and private (0600)")
    if not path.exists():
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            return private_db(path, project)
    return path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def chat_notice(result, *, live):
    """Prepare text only; delivery and its ack remain the coordinator's actions."""
    output = {"channel": "orca_chat", "deliver": False, "text": None,
              "notice_id": None, "permission_decision": "unknown"}
    if not live:
        return dict(output, reason="historical_observation_not_live")
    if result.get("action") not in {"deliver_pending_notice", "replay"}:
        return dict(output, reason="no_current_notice")
    notice = result.get("notice")
    if not notice or result.get("state") != notice["state"]:
        return dict(output, reason="no_current_notice")
    dispatch = notice["dispatch_id"]
    if notice["state"] == "pending":
        text = f"작업 {dispatch}에서 사용자 입력 대기가 관측되었습니다. 해당 Orca worker의 승인·질문 창에서 내용을 확인해 주세요."
        if notice["reason"] == "pending_identity_unavailable":
            text += " 원본 요청 ID가 없어 이전 알림과 같은 요청인지 확인하지 못했습니다."
    elif notice["state"] == "clear":
        text = f"작업 {dispatch}의 사용자 입력 대기는 더 이상 관측되지 않습니다. 승인 또는 거부 결과를 뜻하지 않습니다."
    elif notice["state"] == "ended":
        text = f"작업 {dispatch}의 worker 종료를 확인했습니다. 승인 또는 거부 결과는 확인하지 않았습니다."
    else:
        return dict(output, reason="no_current_notice")
    text += f" 관측 시각: {notice['observed_at']}"
    return dict(output, deliver=True, text=text, notice_id=notice["id"],
                reason="delivery_required_before_ack")


class WaitStore:
    def __init__(self, path, project):
        self.path = private_db(path, project)
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("BEGIN IMMEDIATE")
            application = self.db.execute("PRAGMA application_id").fetchone()[0]
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if (application, version) != (0x41575754, 1) and (application or version or tables):
                raise ValueError("Not a supported approval wait database")
            self.db.execute("CREATE TABLE IF NOT EXISTS waits (key TEXT PRIMARY KEY, observed_at TEXT, event_hash TEXT, state TEXT, signature TEXT, notice TEXT, acked INTEGER, last_probe TEXT)")
            self.db.execute("PRAGMA application_id=1096243028")
            self.db.execute("PRAGMA user_version=1")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def observe(self, event):
        event = validate(event)
        key = digest([event["scope"], event["dispatch_id"]])
        fingerprint = digest(event)
        signature = digest([event["source"], event["request_id"], event["revision"], event["kind"]])
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute("SELECT * FROM waits WHERE key=?", (key,)).fetchone()
            status = event["state"]
            result = {"state": status, "action": "none", "notice": None,
                      "permission_decision": "unknown", "scope": event["scope"],
                      "dispatch_id": event["dispatch_id"]}
            if old and event["observed_at"] <= old["observed_at"]:
                if event["observed_at"] == old["observed_at"] and old["event_hash"] == fingerprint:
                    result.update(action="replay", notice=json.loads(old["notice"]) if old["notice"] and not old["acked"] else None)
                else:
                    result["action"] = "stale_or_conflicting_observation"
                result["state"] = old["last_probe"]
                self.db.commit()
                return result
            if old and old["state"] == "ended" and status != "ended":
                result.update(state="ended", action="inspect_conflicting_lifecycle")
                self.db.commit()
                return result
            same = (old and status == old["state"] and
                    (status != "pending" or (event["request_id"] is not None and old["signature"] == signature)))
            notice = json.loads(old["notice"]) if old and old["notice"] else None
            acked = old["acked"] if old else 1
            stored_state, stored_signature = status, signature
            if status == "unknown":
                # Incomplete probes cannot close or replace a previously known wait/notice.
                stored_state = old["state"] if old else "unknown"
                stored_signature = old["signature"] if old else None
                result["action"] = "inspect_unknown"
            elif same:
                result["action"] = "suppressed" if acked else "deliver_pending_notice"
            else:
                reason = None
                if status == "pending":
                    reason = "new_or_changed_request" if event["request_id"] else "pending_identity_unavailable"
                elif old and old["state"] == "pending":
                    reason = "wait_no_longer_observed" if status == "clear" else "worker_exited"
                notice = ({"id": str(uuid.uuid4()), "scope": event["scope"], "dispatch_id": event["dispatch_id"],
                           "state": status, "kind": event["kind"],
                           "reason": reason, "observed_at": event["observed_at"]} if reason else None)
                acked = 0 if notice else 1
                result["action"] = "deliver_pending_notice" if notice else "none"
            self.db.execute("INSERT INTO waits VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET observed_at=excluded.observed_at,event_hash=excluded.event_hash,state=excluded.state,signature=excluded.signature,notice=excluded.notice,acked=excluded.acked,last_probe=excluded.last_probe",
                            (key, event["observed_at"], fingerprint, stored_state, stored_signature,
                             json.dumps(notice) if notice else None, acked, status))
            result["notice"] = notice if not acked else None
            self.db.commit()
            return result
        except BaseException:
            self.db.rollback()
            raise

    def ack(self, notice_id):
        identifier(notice_id, "notice_id")
        with self.db:
            cursor = self.db.execute("UPDATE waits SET acked=1 WHERE json_extract(notice,'$.id')=?", (notice_id,))
        if not cursor.rowcount:
            raise ValueError("Notice is unknown or superseded; it was not acknowledged")
        return {"acknowledged": notice_id, "permission_decision": "unknown"}

    def pending(self):
        return [{"notice": json.loads(row[0]), "latest_observation": row[1]}
                for row in self.db.execute("SELECT notice,last_probe FROM waits WHERE acked=0 AND notice IS NOT NULL")]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--chat", action="store_true", help="Prepare an Orca chat notice; never sends or acknowledges it")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("observe").add_argument("event", type=Path)
    orca = sub.add_parser("orca")
    orca.add_argument("--scope", required=True, help="Explicit host identity, e.g. orca.local")
    orca.add_argument("--dispatch", required=True)
    source = orca.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path)
    source.add_argument("--live", action="store_true")
    orca.add_argument("--observed-at", help="Required snapshot capture time; never use the import time")
    orca.add_argument("--orca-cli", help="Pinned local executable; no fallback or remote routing")
    sub.add_parser("pending")
    sub.add_parser("ack").add_argument("notice_id")
    args = parser.parse_args(argv)
    store = None
    try:
        project = args.project.expanduser().resolve(strict=True)
        if not project.is_dir():
            raise ValueError("Project must be a directory")
        if args.chat and args.command not in {"orca", "observe"}:
            raise ValueError("Chat preparation requires an observation")
        event = None
        if args.command == "observe":
            event = validate(read_json(args.event))
        elif args.command == "orca":
            if args.snapshot:
                if not args.observed_at or args.orca_cli:
                    raise ValueError("Snapshot requires capture --observed-at and no CLI")
                event = normalize_orca(read_json(args.snapshot), args.scope, args.dispatch, args.observed_at)
            else:
                if args.observed_at or os.environ.get("ORCA_ENVIRONMENT") or os.environ.get("ORCA_PAIRING_CODE"):
                    raise ValueError("Live probe supports local routing only; no timestamp override")
                cli = args.orca_cli if args.orca_cli is not None else executable_for("orca", os.environ)
                if not cli.strip():
                    raise ValueError("Explicit Orca CLI must not be empty")
                event = probe_orca(cli, project, args.scope, args.dispatch)
        store = WaitStore(args.state_db, project)
        if event:
            result = store.observe(event)
        elif args.command == "ack":
            result = store.ack(args.notice_id)
        else:
            result = {"pending": store.pending()}
        if args.chat:
            result["chat"] = chat_notice(result, live=args.command == "orca" and args.live)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired):
        print("Wait handling failed; check inputs or use formal access approval. State was not assumed clear.", file=sys.stderr)
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
