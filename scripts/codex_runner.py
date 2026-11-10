"""Stream Codex events with bounded buffering and explicit process cleanup."""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

from run_records import UsageCollector, codex_version, utc_now
from run_diagnostics import EventDiagnostics

MAX_EVENT_BYTES = 8 * 1024 * 1024
STOP_GRACE_SECONDS = 2


def stop_process(process, signum=signal.SIGTERM):
    if process is None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signum)
        else:
            process.terminate()
        try:
            process.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        if os.name == "posix":
            # Parent exit does not imply descendant exit. Clean up the same group.
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    process.wait()


def read_events(stream, events, cancelled):
    def emit(kind, value=None):
        while not cancelled.is_set():
            try:
                events.put((kind, value), timeout=0.1)
                return
            except queue.Full:
                pass

    try:
        while not cancelled.is_set():
            raw = stream.readline(MAX_EVENT_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_EVENT_BYTES:
                emit("issue", "oversized_event")
                while raw and not raw.endswith(b"\n") and not cancelled.is_set():
                    raw = stream.readline(MAX_EVENT_BYTES + 1)
                continue
            try:
                event = json.loads(raw.decode("utf-8"))
            except (UnicodeError, ValueError, RecursionError):
                emit("issue", "invalid_event")
                continue
            # Tool bodies need not travel to the collector or be retained in a queue.
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                emit("issue", "invalid_event")
            elif event["type"] in {"turn.started", "turn.completed", "turn.failed"}:
                emit("event", {"type": event["type"], "usage": event.get("usage")})
            elif event["type"] == "item.completed":
                item = event.get("item")
                if isinstance(item, dict):
                    emit("item", (item.get("id"), item.get("type")))
                    if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        emit("message", item["text"])
    except OSError:
        emit("io_error")
    finally:
        emit("eof")


def execute_run(command, prompt, project, record, store=None):
    collector = UsageCollector()
    diagnostics = EventDiagnostics()
    process = None
    workers = []
    events = queue.Queue(maxsize=32)
    cancelled = threading.Event()
    write_errors = []
    last_message = None
    received_signal = None
    previous_handlers = {}
    start = time.monotonic()
    timeout = record["timeout_seconds"]
    deadline = start + timeout if timeout is not None else None
    if store:
        store.write(record)  # A failed initial write prevents a model call.

    def handle_signal(signum, frame):
        nonlocal received_signal
        # A flag also covers signals arriving while Popen is being constructed.
        if received_signal is None:
            received_signal = signum

    def stop_reason():
        if received_signal is not None:
            return "sigint" if received_signal == signal.SIGINT else "sigterm"
        if deadline is not None and time.monotonic() >= deadline:
            return "timeout"
        return None

    reason = None
    code = 2
    eof = False
    print(f'workflow: started run {record["run_id"]}', file=sys.stderr)
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, handle_signal)
        record["runtime_version"] = codex_version()
        reason = stop_reason()
        if reason is None:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       cwd=project.resolve(), start_new_session=os.name == "posix")

            def write_prompt():
                try:
                    process.stdin.write(prompt.encode("utf-8"))
                except (OSError, UnicodeError):
                    write_errors.append("input_write_failed")
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        write_errors.append("input_write_failed")

            workers = [threading.Thread(target=write_prompt, daemon=True),
                       threading.Thread(target=read_events, args=(process.stdout, events, cancelled), daemon=True)]
            for worker in workers:
                worker.start()
            eof = False
            while not eof or process.poll() is None:
                reason = stop_reason()
                if reason:
                    break
                try:
                    kind, value = events.get(timeout=0.1)
                except queue.Empty:
                    continue
                if kind == "event":
                    collector.consume(value)
                elif kind == "item":
                    diagnostics.consume(*value)
                elif kind == "message":
                    last_message = value
                elif kind == "issue":
                    collector.issues.add(value)
                elif kind == "io_error":
                    reason = "runner_io_error"
                    break
                elif kind == "eof":
                    eof = True
        if reason:
            stop_process(process, signal.SIGINT if reason == "sigint" else signal.SIGTERM)
            record["execution_status"] = "timed_out" if reason == "timeout" else "failed" if reason == "runner_io_error" else "interrupted"
            record["failure_reason"] = reason
            code = {"sigint": 130, "sigterm": 143, "timeout": 124, "runner_io_error": 2}[reason]
        else:
            code = process.wait()
            if code != 0:
                record["execution_status"] = "failed"
                record["failure_reason"] = "process_exit"
            elif collector.last_terminal != "completed" or write_errors:
                record["execution_status"] = "failed"
                record["failure_reason"] = "input_write_failed" if write_errors else "missing_success_event"
                code = 2
            else:
                record["execution_status"] = "succeeded"
    except OSError:
        stop_process(process)
        record["execution_status"] = "failed"
        record["failure_reason"] = "runner_io_error" if process else "launch_failed"
        code = 2
    finally:
        cancelled.set()
        for worker in workers:
            worker.join(timeout=3)
        if process and not any(worker.is_alive() for worker in workers):
            process.stdout.close()
        record["exit_code"] = process.returncode if process else None
        collector.issues.update(write_errors)
        if write_errors and record["execution_status"] == "succeeded":
            record["execution_status"] = "failed"
            record["failure_reason"] = "input_write_failed"
            code = 2
        record["finished_at"] = utc_now()
        record["duration_ms"] = max(0, round((time.monotonic() - start) * 1000))
        record["usage"] = collector.result(record["execution_status"])
        record.setdefault("diagnostics", {"prompt": None})["events"] = diagnostics.result(
            complete=eof and record["execution_status"] == "succeeded" and not collector.issues,
            started=process is not None,
        )
        try:
            if store:
                try:
                    destination = store.write(record)
                    print(f"workflow: record saved to {destination}", file=sys.stderr)
                except OSError:
                    print("workflow: final record could not be saved; inspect the run before retrying", file=sys.stderr)
                    code = 2
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    if last_message is not None:
        print(last_message)
    usage = record["usage"]
    print(f'workflow: execution={record["execution_status"]} usage={usage["status"]} '
          f'input={usage["input_tokens"]} output={usage["output_tokens"]} '
          f'cached_input={usage["cached_input_tokens"]} total={usage["total_tokens"]}', file=sys.stderr)
    return code if code >= 0 else 128 - code
