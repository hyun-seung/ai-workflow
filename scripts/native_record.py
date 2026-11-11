#!/usr/bin/env python3
"""Inspect one explicit Orca/Codex session snapshot; never execute or approve work."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from prepare_handoff import private_destination

MAX_BYTES = 128 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024 * 1024
ID = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}"
FIELDS = ("reviewer", "approval_policy", "model", "effort", "sandbox_mode")
TOKENS = ("input_tokens", "output_tokens", "cached_input_tokens",
          "reasoning_output_tokens", "total_tokens")
PREAMBLE = "You are working inside Orca, a multi-agent IDE. You are a dispatched worker."


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(ID, value):
        raise ValueError("invalid_identifier")
    return value


def label(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:+-]{0,199}", value) else None


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat() if dt.tzinfo else None
    except ValueError:
        return None


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def reject_constant(_):
    raise ValueError("nonfinite_json")


def assignment(payload):
    """Read only the generated header; task text/references cannot bind a dispatch."""
    if payload.get("role") != "user":
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    parts = [b.get("text", "") for b in content
             if isinstance(b, dict) and b.get("type") == "input_text" and isinstance(b.get("text"), str)]
    text = "\n".join(parts)
    if not text.startswith(PREAMBLE + "\n"):
        return None
    header, separator, _ = text.partition("=== TASK ===")
    if not separator or len(header) > 16384:
        raise ValueError("unsupported_assignment_header")
    tasks = re.findall(r"^Your task ID is: (" + ID + r")$", header, re.M)
    sessions = re.findall(r"^Your Orca session ID is: (" + ID + r")$", header, re.M)
    dispatches = set(re.findall(r"--dispatch-id (" + ID + r")(?![A-Za-z0-9_.:-])", header))
    command_tasks = set(re.findall(r"--task-id (" + ID + r")(?![A-Za-z0-9_.:-])", header))
    if len(tasks) != 1 or len(sessions) != 1 or len(dispatches) != 1 or command_tasks != set(tasks):
        raise ValueError("ambiguous_assignment_header")
    return {"task_id": tasks[0], "dispatch_id": dispatches.pop(), "orca_session_id": sessions[0]}


def observed(payload):
    sandbox = payload.get("sandbox_policy")
    sandbox = sandbox if isinstance(sandbox, dict) else {}
    policy = payload.get("approval_policy")
    return {
        "reviewer": label(payload.get("approvals_reviewer")),
        "approval_policy": label(policy) if isinstance(policy, str) else None,
        "model": label(payload.get("model")), "effort": label(payload.get("effort")),
        "sandbox_mode": label(sandbox.get("type")),
        "network_access": sandbox.get("network_access") if type(sandbox.get("network_access")) is bool else None,
    }


def tokens(info):
    values = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(values, dict) or not values:
        return None, "missing_usage"
    result = {k: values.get(k) for k in TOKENS}
    if all(v is None for v in result.values()):
        return None, "missing_usage"
    if any(v is not None and (type(v) is not int or v < 0) for v in result.values()):
        return None, "invalid_usage"
    for detail, total in (("cached_input_tokens", "input_tokens"), ("reasoning_output_tokens", "output_tokens")):
        if result[detail] is not None and result[total] is not None and result[detail] > result[total]:
            return None, "invalid_usage"
    if result["input_tokens"] is None or result["output_tokens"] is None:
        return result, "incomplete_usage"
    total = result["input_tokens"] + result["output_tokens"]
    if result["total_tokens"] not in (None, total):
        return None, "invalid_usage"
    result["total_tokens"] = total
    return result, None


def launch_requested(snapshot, target):
    """An explicit worker-show receipt is optional; CLI expectations are not launch facts."""
    unknown = {"model": None, "effort": None, "source": None, "status": "unavailable"}
    if snapshot is None:
        return unknown
    path = Path(snapshot).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_LINE_BYTES:
        raise ValueError("invalid_launch_snapshot_file")
    before = path.stat()
    with path.open('rb') as stream:
        raw = stream.read(MAX_LINE_BYTES + 1)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("launch_snapshot_changed_during_read")
    if len(raw) > MAX_LINE_BYTES:
        raise ValueError("launch_snapshot_limit")
    try:
        row = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        result = row['result']
        dispatch = result['dispatch']
        worker = result['worker']
        requested = worker['startOptions']['launch']['requested']
        expected_incarnation = 'structured:' + target['orca_session_id'].removeprefix('orca_session_id:')
        if (row['ok'] is not True or dispatch['id'] != target['dispatch_id'] or
                dispatch['taskId'] != target['task_id'] or worker['dispatchId'] != target['dispatch_id'] or
                dispatch['processIncarnation'] != expected_incarnation or requested['agent'] != 'codex'):
            raise ValueError('launch_snapshot_identity_mismatch')
        values = {k: requested[k] for k in ('model', 'effort')}
        if any(v is not None and label(v) is None for v in values.values()):
            raise ValueError('invalid_requested_profile')
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, AttributeError):
        raise ValueError('invalid_or_mismatched_launch_snapshot') from None
    return dict(values, status='observed', source={'kind': 'orca.worker-show.launch.requested',
                'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()})


def inspect_session(project, session_file, session_id, task_id, dispatch_id, expected=None, worker_snapshot=None):
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError("project_not_directory")
    for value in (session_id, task_id, dispatch_id):
        identifier(value)
    expected = expected or {}
    if set(expected) - set(FIELDS) or any(label(v) is None for v in expected.values()):
        raise ValueError("invalid_expectation")
    path = Path(session_file).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("session_not_regular_file")
    contexts, assignments, measurements = [], [], []
    meta = None
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError("session_file_limit")
        n = 0
        while True:
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            n += 1
            size += len(raw)
            if len(raw) > MAX_LINE_BYTES or size > MAX_BYTES:
                raise ValueError("session_file_limit")
            digest.update(raw)
            if not raw.strip():
                continue
            try:
                row = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise ValueError(f"invalid_json_line_{n}") from None
            if not isinstance(row, dict) or not isinstance(row.get("type"), str) or not isinstance(row.get("payload"), dict):
                raise ValueError(f"invalid_event_line_{n}")
            p = row["payload"]
            kind = row["type"]
            if meta is None and kind != "session_meta":
                raise ValueError("missing_initial_session_meta")
            if kind == "session_meta":
                if meta is not None:
                    raise ValueError("multiple_session_meta")
                # Never count a separate reviewer/guardian as the role worker.
                if p.get("source") != "vscode" or p.get("originator") != "orca_desktop":
                    raise ValueError("not_supported_orca_worker_source")
                if p.get("id") != session_id or not isinstance(p.get("cwd"), str) or Path(p["cwd"]).resolve() != project:
                    raise ValueError("session_identity_or_project_mismatch")
                meta = {"id": session_id, "source": "vscode", "originator": "orca_desktop",
                        "cli_version": label(p.get("cli_version")), "line": n}
            elif kind == "turn_context":
                contexts.append({"line": n, "time": timestamp(row.get("timestamp")), "values": observed(p)})
            elif kind == "response_item":
                binding = assignment(p)
                if binding:
                    assignments.append(dict(binding, line=n, context_line=contexts[-1]["line"] if contexts else None))
            elif kind == "event_msg" and p.get("type") == "token_count":
                values, issue = tokens(p.get("info"))
                measurements.append({"line": n, "time": timestamp(row.get("timestamp")),
                                     "values": values, "issue": issue})
        after = os.fstat(stream.fileno())
    current = path.stat()
    fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(current):
        raise ValueError("session_changed_during_read")
    targets = [a for a in assignments if a["task_id"] == task_id and a["dispatch_id"] == dispatch_id]
    if len(targets) != 1:
        raise ValueError("missing_or_ambiguous_assignment")
    target = targets[0]
    requested = launch_requested(worker_snapshot, target)
    start = target["context_line"]
    index = assignments.index(target)
    end = assignments[index + 1]["context_line"] if index + 1 < len(assignments) else n + 1
    if start is None or end is None or end <= target["line"] or (index and start <= assignments[index - 1]["line"]):
        raise ValueError("assignment_context_boundary_unknown")
    selected = [c for c in contexts if start <= c["line"] < end]
    checks = {}
    comparisons = [(field, field, wanted) for field, wanted in expected.items()]
    comparisons += [('launch_' + field, field, requested[field]) for field in ('model', 'effort')
                    if requested[field] is not None]
    for key, field, wanted in comparisons:
        values = [c["values"][field] for c in selected]
        state = "fail" if any(v is not None and v != wanted for v in values) else "unverified" if None in values else "pass"
        checks[key] = {"expected": wanted, "status": state}
    verdict = "fail" if any(c["status"] == "fail" for c in checks.values()) else "unverified" if any(c["status"] == "unverified" for c in checks.values()) else "pass" if checks else "not_requested"
    relevant = [m for m in measurements if target["line"] < m["line"] < end]
    last = next((m for m in reversed(relevant) if m["values"] is not None), None)
    issues = {m["issue"] for m in relevant if m["issue"]}
    previous = None
    for m in measurements:
        if m["line"] >= end:
            break
        value = m["values"]
        if value is not None:
            if previous and any(value[k] is not None and previous[k] is not None and value[k] < previous[k]
                                for k in ("input_tokens", "output_tokens")):
                issues.add("non_monotonic_session_usage")
            previous = value
    if not relevant:
        issues.add("missing_usage")
    usage_status = "unavailable" if last is None else "partial" if issues else "measured"
    usage = {"status": usage_status, "scope": "session_cumulative", "task_attributed": False,
             "measurement_key": "codex.session:" + session_id,
             "source": "event_msg.token_count.info.total_token_usage",
             "line": last["line"] if last else None, "time": last["time"] if last else None,
             "tokens": last["values"] if last else dict.fromkeys(TOKENS), "issues": sorted(issues),
             "assignment_count_in_snapshot": len(assignments),
             "includes_coordinator_or_reviewers": False}
    return {"schema_version": 1, "kind": "orca_codex_metadata", "recorded_at": datetime.now(timezone.utc).isoformat(),
            "environment": "orca", "provider": "codex", "project": str(project), "binding": target,
            "session": meta, "source": {"path": str(path), "sha256": digest.hexdigest(), "bytes": size,
                                         "lines": n, "assignment_end_exclusive": end},
            "requested": requested, "expected": {k: expected.get(k) for k in FIELDS}, "observed_contexts": selected,
            "verification": {"status": verdict, "checks": checks}, "usage": usage,
            "live_state_verified": False, "task_acceptance_evaluated": False,
            "limitations": ["snapshot_only", "no_permission_grant", "no_task_token_attribution",
                            "no_coordinator_or_reviewer_usage", "not_a_trace_bundle"]}


def write_record(record, output, project):
    destination = private_destination(output, Path(project).resolve(strict=True))
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        destination.unlink()
        raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "session-file", "session-id", "task-id", "dispatch-id", "output"):
        parser.add_argument("--" + name, required=True)
    for field in FIELDS:
        parser.add_argument("--expect-" + field.replace("_", "-"))
    parser.add_argument('--worker-snapshot', help='Optional explicit Orca worker-show --full JSON receipt')
    args = parser.parse_args(argv)
    expected = {k: getattr(args, "expect_" + k) for k in FIELDS if getattr(args, "expect_" + k) is not None}
    try:
        record = inspect_session(args.project, args.session_file, args.session_id, args.task_id, args.dispatch_id,
                                 expected, args.worker_snapshot)
        output = write_record(record, args.output, args.project)
    except (OSError, ValueError) as error:
        # OS exception strings may contain private paths; do not print them.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("Native record failed: " + message, file=sys.stderr)
        return 2
    print(json.dumps({"output": str(output), "verification": record["verification"]["status"],
                      "usage_status": record["usage"]["status"], "usage_scope": record["usage"]["scope"]}))
    return 1 if record["verification"]["status"] in ("fail", "unverified") else 0


if __name__ == "__main__":
    raise SystemExit(main())
