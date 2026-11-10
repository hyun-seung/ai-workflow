"""Read local records and aggregate measured runs without model calls or writes."""

import json
from pathlib import Path
import re
import uuid

STATUSES = ("running", "succeeded", "failed", "interrupted", "timed_out")
USAGES = ("measured", "partial", "unavailable")


def count(value):
    return type(value) is int and value >= 0


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def checked_record(record):
    if not isinstance(record, dict):
        raise ValueError("Invalid record")
    record["run_id"] = str(uuid.UUID(record["run_id"]))
    for field, pattern in (("task_id", r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"),
                           ("role", r"[a-z][a-z0-9_-]*")):
        if not isinstance(record.get(field), str) or not re.fullmatch(pattern, record[field]):
            raise ValueError("Invalid identifier")
    requested = record["requested"]
    if not isinstance(requested, dict) or not isinstance(requested.get("model"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", requested["model"]):
        raise ValueError("Invalid model")
    if requested.get("effort") not in ("low", "medium", "high", "xhigh", "max"):
        raise ValueError("Invalid effort")
    if record["execution_status"] not in STATUSES:
        raise ValueError("Invalid execution status")
    if record.get("duration_ms") is not None and not count(record["duration_ms"]):
        raise ValueError("Invalid duration")
    usage = record["usage"]
    if usage["status"] not in USAGES or usage.get("source") != "codex.exec.json.turn.completed":
        raise ValueError("Invalid usage")
    if usage.get("scope") not in ("single_turn", "last_reported_turn", "unknown"):
        raise ValueError("Invalid scope")
    for field in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_output_tokens"):
        if usage.get(field) is not None and not count(usage[field]):
            raise ValueError("Invalid token count")
    for detail, total in (("cached_input_tokens", "input_tokens"), ("reasoning_output_tokens", "output_tokens")):
        if count(usage.get(detail)) and count(usage.get(total)) and usage[detail] > usage[total]:
            raise ValueError("Invalid token subset")
    if count(usage.get("input_tokens")) and count(usage.get("output_tokens")):
        if usage.get("total_tokens") != usage["input_tokens"] + usage["output_tokens"]:
            raise ValueError("Inconsistent total")
    if usage["status"] == "measured" and not (
        record["execution_status"] == "succeeded" and usage["scope"] == "single_turn"
        and count(usage.get("input_tokens")) and count(usage.get("output_tokens")) and not usage.get("issues")
    ):
        raise ValueError("Inconsistent measured status")
    return record


def empty_totals():
    return {
        "runs": 0, "execution_counts": dict.fromkeys(STATUSES, 0), "usage_counts": dict.fromkeys(USAGES, 0),
        "measured_input_tokens": None, "measured_output_tokens": None, "measured_total_tokens": None,
        "runs_with_cache_details": 0, "cached_input_tokens": None, "input_tokens_with_cache_details": None,
        "uncached_input_tokens_with_cache_details": None, "cache_ratio_percent": None,
        "timed_runs": 0, "duration_ms": None, "diagnostic_runs": 0,
        "observed_completed_tool_items": None, "prompt_characters_min": None, "prompt_characters_max": None,
    }


def add(totals, record):
    totals["runs"] += 1
    totals["execution_counts"][record["execution_status"]] += 1
    usage = record["usage"]
    totals["usage_counts"][usage["status"]] += 1
    if usage["status"] == "measured":
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            totals["measured_" + field] = (totals["measured_" + field] or 0) + usage[field]
        if count(usage.get("cached_input_tokens")):
            totals["runs_with_cache_details"] += 1
            totals["cached_input_tokens"] = (totals["cached_input_tokens"] or 0) + usage["cached_input_tokens"]
            totals["input_tokens_with_cache_details"] = (totals["input_tokens_with_cache_details"] or 0) + usage["input_tokens"]
            totals["uncached_input_tokens_with_cache_details"] = totals["input_tokens_with_cache_details"] - totals["cached_input_tokens"]
            denominator = totals["input_tokens_with_cache_details"]
            totals["cache_ratio_percent"] = round(100 * totals["cached_input_tokens"] / denominator, 2) if denominator else None
    if count(record.get("duration_ms")):
        totals["timed_runs"] += 1
        totals["duration_ms"] = (totals["duration_ms"] or 0) + record["duration_ms"]
    diagnostics = record.get("diagnostics")
    if isinstance(diagnostics, dict):
        events = diagnostics.get("events")
        if isinstance(events, dict) and events.get("status") in ("observed", "partial") and count(events.get("completed_tool_items")):
            totals["diagnostic_runs"] += 1
            totals["observed_completed_tool_items"] = (totals["observed_completed_tool_items"] or 0) + events["completed_tool_items"]
        prompt = diagnostics.get("prompt")
        if isinstance(prompt, dict) and count(prompt.get("prompt_characters")):
            value = prompt["prompt_characters"]
            totals["prompt_characters_min"] = value if totals["prompt_characters_min"] is None else min(value, totals["prompt_characters_min"])
            totals["prompt_characters_max"] = value if totals["prompt_characters_max"] is None else max(value, totals["prompt_characters_max"])


def summarize(directory, task_id=None, parent_task_id=None, role=None, execution_status=None):
    if execution_status is not None and (not isinstance(execution_status, str) or execution_status not in STATUSES):
        raise ValueError("Invalid execution status filter")
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("Records directory does not exist")
    scanned = {"json_files": 0, "invalid_files": 0, "unsupported_versions": 0,
               "ignored_symlinks": 0, "duplicate_files": 0, "conflicting_run_ids": 0}
    by_id = {}
    canonical = {}
    conflicts = set()
    for path in sorted(directory.glob("*.json")):
        scanned["json_files"] += 1
        if path.is_symlink():
            scanned["ignored_symlinks"] += 1
            continue
        try:
            with path.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Oversized record")
            record = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
            if not isinstance(record, dict):
                raise ValueError("Invalid record")
            if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
                scanned["unsupported_versions"] += 1
                continue
            record = checked_record(record)
            encoded = json.dumps(record, sort_keys=True, allow_nan=False)
            run_id = record["run_id"]
            if run_id in by_id:
                if canonical[run_id] == encoded:
                    scanned["duplicate_files"] += 1
                else:
                    conflicts.add(run_id)
            else:
                by_id[run_id] = record
                canonical[run_id] = encoded
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            scanned["invalid_files"] += 1
    scanned["conflicting_run_ids"] = len(conflicts)
    totals = empty_totals()
    groups = {}
    for run_id, record in by_id.items():
        if run_id in conflicts or any(value is not None and record.get(key) != value
                                      for key, value in (("task_id", task_id), ("parent_task_id", parent_task_id),
                                                         ("role", role), ("execution_status", execution_status))):
            continue
        key = (record["role"], record["requested"]["model"], record["requested"]["effort"])
        group = groups.setdefault(key, empty_totals())
        add(group, record)
        add(totals, record)
    complete = not any(scanned[name] for name in ("invalid_files", "unsupported_versions", "ignored_symlinks", "conflicting_run_ids"))
    return {"scan_complete": complete, "scan": scanned, "totals": totals,
            "groups": [{"role": key[0], "requested_model": key[1], "requested_effort": key[2], **value}
                       for key, value in sorted(groups.items())]}


def print_summary(result):
    totals = result["totals"]
    def number(value):
        return "미측정" if value is None else f"{value:,}"
    print(f'실행 {totals["runs"]}건 / 사용량 상태 {json.dumps(totals["usage_counts"], ensure_ascii=False)}')
    print("역할 | 요청 모델 / effort | 실행 수 | measured 토큰 | 실패·중단·시간초과")
    print("--- | --- | ---: | ---: | ---:")
    for group in result["groups"]:
        failures = sum(group["execution_counts"][key] for key in ("failed", "interrupted", "timed_out"))
        print(f'{group["role"]} | {group["requested_model"]} / {group["requested_effort"]} | {group["runs"]} | {number(group["measured_total_tokens"])} | {failures}')
    print(f'측정 완료 실행 합계: 입력 {number(totals["measured_input_tokens"])} / 출력 {number(totals["measured_output_tokens"])} / 전체 {number(totals["measured_total_tokens"])}')
    ratio = "미측정" if totals["cache_ratio_percent"] is None else f'{totals["cache_ratio_percent"]}%'
    print(f'캐시 상세 {totals["runs_with_cache_details"]}건: 캐시 {number(totals["cached_input_tokens"])} / 비캐시 {number(totals["uncached_input_tokens_with_cache_details"])} / 비율 {ratio}')
    print(f'시간 확인 {totals["timed_runs"]}건의 누적 시간: {number(totals["duration_ms"])} ms (병렬 실행의 실제 경과 시간과 다름)')
    print(f'진단 {totals["diagnostic_runs"]}건: 관측된 완료 도구 작업 {number(totals["observed_completed_tool_items"])} / 전달 프롬프트 문자수 {number(totals["prompt_characters_min"])} ~ {number(totals["prompt_characters_max"])}')
    print('부분 측정·미측정은 토큰 합계에서 제외. 문자수·도구 작업 수는 모델 호출 수나 토큰 수가 아님.')
    print('파일 검사:', json.dumps(result["scan"], ensure_ascii=False))
