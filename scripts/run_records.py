"""Opt-in local records. Never persist prompts, event bodies, or model output."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import uuid

from run_diagnostics import EventDiagnostics

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")
TASK_TYPES = {"development", "research", "design-review", "writing"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_id(value, label):
    if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError(f"{label}: use a non-sensitive ID of 1-64 letters, digits, _, ., -")


class UsageCollector:
    """Use a terminal snapshot; never sum potentially cumulative notifications."""

    def __init__(self):
        self.tokens = dict.fromkeys(TOKEN_FIELDS)
        self.starts = 0
        self.completions = 0
        self.last_terminal = None
        self.last_snapshot = None
        self.issues = set()

    def consume(self, event):
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self.issues.add("invalid_event")
            return
        kind = event["type"]
        if kind == "turn.started":
            self.starts += 1
            self.last_terminal = None
            if self.starts > 1:
                self.issues.add("multiple_turns")
        elif kind == "turn.failed":
            self.last_terminal = "failed"
            self.issues.add("failed_turn")
        elif kind == "turn.completed":
            self.last_terminal = "completed"
            usage = event.get("usage")
            tokens = dict.fromkeys(TOKEN_FIELDS)
            if not isinstance(usage, dict):
                self.issues.add("missing_usage")
            else:
                for field in TOKEN_FIELDS:
                    value = usage.get(field)
                    if type(value) is int and value >= 0:
                        tokens[field] = value
                    elif value is not None:
                        self.issues.add("invalid_usage")
                for detail, total in (("cached_input_tokens", "input_tokens"),
                                      ("reasoning_output_tokens", "output_tokens")):
                    if tokens[detail] is not None and tokens[total] is not None and tokens[detail] > tokens[total]:
                        tokens[detail] = None
                        self.issues.add("invalid_usage")
            snapshot = tuple(tokens.values())
            if self.completions and snapshot != self.last_snapshot:
                self.issues.add("conflicting_completions")
            self.completions += 1
            self.last_snapshot = snapshot
            self.tokens = tokens

    def result(self, execution_status):
        issues = set(self.issues)
        if self.last_terminal != "completed":
            issues.add("missing_completed_turn")
        if execution_status != "succeeded":
            issues.add("execution_not_successful")
        complete = all(self.tokens[k] is not None for k in ("input_tokens", "output_tokens"))
        any_measured = any(v is not None for v in self.tokens.values())
        status = "measured" if complete and not issues else "partial" if any_measured else "unavailable"
        total = self.tokens["input_tokens"] + self.tokens["output_tokens"] if complete else None
        scope = "last_reported_turn" if self.starts > 1 or "conflicting_completions" in issues else "single_turn"
        return {
            "status": status, "source": "codex.exec.json.turn.completed",
            "scope": scope if any_measured else "unknown", **self.tokens,
            "total_tokens": total, "issues": sorted(issues),
        }


def validate_records_dir(path, forbidden_roots):
    path = Path(path).expanduser().resolve()
    if any(path.is_relative_to(Path(root).resolve()) for root in forbidden_roots):
        raise ValueError("Choose a records directory outside the template and target project")
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise ValueError("Execution records must be outside Git working trees")
    if path.exists():
        if not path.is_dir():
            raise ValueError("Records path must be a directory")
        if os.name == "posix":
            info = path.stat()
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("Use an owned, private records directory (POSIX mode 0700)")
    return path


class JsonRecordStore:
    def __init__(self, directory, forbidden_roots):
        self.directory = validate_records_dir(directory, forbidden_roots)
        missing = []
        cursor = self.directory
        while not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for path in reversed(missing):
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
        self.directory = validate_records_dir(self.directory, forbidden_roots)

    def write(self, record):
        run_id = str(uuid.UUID(record["run_id"]))
        destination = self.directory / f"{run_id}.json"
        fd, temporary = tempfile.mkstemp(prefix=".record-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination


def codex_version():
    try:
        result = subprocess.run(["codex", "--version"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=5, check=False)
        value = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r"codex-cli [0-9][A-Za-z0-9.+_-]{0,79}", value):
            return value
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        pass
    return None


def new_record(role_name, role, task_id=None, result_refs=(), parent_task_id=None,
               task_type=None, timeout_seconds=None, prompt_stats=None):
    run_id = str(uuid.uuid4())
    return {
        "schema_version": 1, "run_id": run_id, "task_id": task_id or run_id,
        "parent_task_id": parent_task_id, "task_type": task_type,
        "role": role_name, "adapter": "codex", "runtime_version": None,
        "requested": {key: role[key] for key in ("model", "effort", "sandbox")},
        "observed": {"model": None, "effort": None},
        "timeout_seconds": timeout_seconds,
        "started_at": utc_now(), "finished_at": None, "duration_ms": None,
        "execution_status": "running", "exit_code": None, "failure_reason": None,
        "task_verification": "not_evaluated", "result_refs": list(result_refs),
        "usage": UsageCollector().result("running"),
        "diagnostics": {"prompt": prompt_stats, "events": EventDiagnostics().result(started=False)},
    }
