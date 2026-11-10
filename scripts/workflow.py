#!/usr/bin/env python3
"""Load reusable role settings and launch one Codex run. Python 3.10+, stdlib only."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import sys

from codex_runner import execute_run
from context_input import load_context
from run_records import JsonRecordStore, TASK_TYPES, new_record, validate_id, validate_records_dir
from run_summary import STATUSES, print_summary, summarize

TEMPLATE_ROOT = Path(__file__).resolve().parent.parent
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
SANDBOXES = {"read-only", "workspace-write"}
ROLE_FIELDS = {"model", "effort", "sandbox", "instructions"}
CONFIG_FIELDS = {"$schema", "version", "adapter", "instructions", "roles"}


class ConfigError(ValueError):
    pass


def object_keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ConfigError(f"{label}: expected an object")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{label}: unsupported keys: {', '.join(sorted(unknown))}")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def instruction_paths(value, root, label):
    if not isinstance(value, list):
        raise ConfigError(f"{label}: expected a list of relative Markdown paths")
    result = []
    for raw in value:
        if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
            raise ConfigError(f"{label}: use relative Markdown paths")
        path = (root / raw).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != ".md":
            raise ConfigError(f"{label}: path must be a Markdown file inside its config directory")
        if not path.is_file():
            raise ConfigError(f"{label}: file not found: {raw}")
        result.append(path)
    return result


def read_layer(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name}: invalid JSON at line {exc.lineno}") from exc
    object_keys(data, CONFIG_FIELDS, path.name)
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ConfigError(f"{path.name}: version must be 1")
    if data.get("adapter", "codex") != "codex":
        raise ConfigError("Only the codex adapter is implemented")
    if "$schema" in data and not isinstance(data["$schema"], str):
        raise ConfigError("$schema must be a string")
    data["instructions"] = instruction_paths(data.get("instructions", []), path.parent, path.name)
    roles = data.get("roles", {})
    if not isinstance(roles, dict):
        raise ConfigError("roles must be an object")
    for name, role in roles.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ConfigError(f"Invalid role name: {name}")
        object_keys(role, ROLE_FIELDS, f"role {name}")
        if not role:
            raise ConfigError(f"role {name}: empty override")
        if "model" in role and (
            not isinstance(role["model"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", role["model"])
        ):
            raise ConfigError(f"role {name}: invalid model ID")
        if "effort" in role and (not isinstance(role["effort"], str) or role["effort"] not in EFFORTS):
            raise ConfigError(f"role {name}: unsupported effort")
        if "sandbox" in role and (not isinstance(role["sandbox"], str) or role["sandbox"] not in SANDBOXES):
            raise ConfigError(f"role {name}: unsupported sandbox")
        if "instructions" in role:
            role["instructions"] = instruction_paths(role["instructions"], path.parent, f"role {name}")
    data["roles"] = roles
    return data


def load_config(template_root, project_root, local_config=None):
    template_root = Path(template_root).resolve()
    project_root = Path(project_root).resolve()
    if not project_root.is_dir():
        raise ConfigError("Project directory does not exist")
    layers = [template_root / "workflow.json"]
    project_config = project_root / ".ai-workflow.json"
    if project_config.exists() or project_config.is_symlink():
        if project_config.is_symlink():
            raise ConfigError("Project config must not be a symlink; use --local-config explicitly")
        layers.append(project_config)
    if local_config is not None:
        layers.append(Path(local_config).resolve())
    result = {"adapter": "codex", "instructions": [], "roles": {}, "sources": layers}
    for index, path in enumerate(layers):
        layer = read_layer(path)
        result["instructions"].extend(layer["instructions"])
        for name, values in layer["roles"].items():
            if index and name not in result["roles"]:
                raise ConfigError(f"Unknown role override: {name}; define new roles in workflow.json")
            result["roles"].setdefault(name, {}).update(values)
    if not result["roles"]:
        raise ConfigError("workflow.json must define at least one role")
    for name, role in result["roles"].items():
        missing = ROLE_FIELDS - role.keys()
        if missing:
            raise ConfigError(f"role {name}: missing fields: {', '.join(sorted(missing))}")
        if not role["instructions"]:
            raise ConfigError(f"role {name}: at least one instruction file is required")
    return result


def prepare_run(config, project_root, role_name, task, context_files=()):
    if role_name not in config["roles"]:
        raise ConfigError(f"Unknown role: {role_name}")
    if not task.strip():
        raise ConfigError("Task must not be empty")
    role = config["roles"][role_name]
    paths = list(dict.fromkeys(config["instructions"] + role["instructions"]))
    prompt = (
        f"현재 역할: {role_name}. 아래 지침과 대상 프로젝트의 기존 지침을 참고해 작업한다.\n"
        "이번 실행에서는 이 역할의 작업만 수행한다. 다른 에이전트를 생성하거나 실행기를 재호출하지 않는다.\n"
        "다른 역할의 작업이 필요하면 인계할 작업과 이유를 결과에 적는다.\n\n"
    )
    instruction_characters = 0
    for index, path in enumerate(paths, 1):
        content = path.read_text(encoding='utf-8')
        instruction_characters += len(content)
        prompt += f"## 적용 문서 {index}\n{content}\n\n"
    context_text, context_stats = load_context(project_root, context_files)
    prompt += context_text
    prompt += f"## 현재 작업 요청\n{task}\n"
    command = [
        "codex", "exec", "--ephemeral", "--json", "--color", "never",
        "--sandbox", role["sandbox"], "--cd", str(Path(project_root).resolve()),
        "--model", role["model"], "--config",
        f'model_reasoning_effort="{role["effort"]}"', "-",
    ]
    stats = {"prompt_characters": len(prompt), "instruction_files": len(paths),
             "instruction_characters": instruction_characters, "task_characters": len(task), **context_stats}
    return command, prompt, paths, stats


def describe(config, selected=None):
    roles = config["roles"]
    if selected is not None:
        if selected not in roles:
            raise ConfigError(f"Unknown role: {selected}")
        roles = {selected: roles[selected]}
    return {
        "adapter": config["adapter"],
        "sources": [str(path) for path in config["sources"]],
        "shared_instructions": [str(path) for path in config["instructions"]],
        "roles": {
            name: {**role, "instructions": [str(path) for path in role["instructions"]]}
            for name, role in roles.items()
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    summary_command = commands.add_parser("summary", help="Read local records without calling a model")
    summary_command.add_argument("--records-dir", required=True, type=Path)
    summary_command.add_argument("--task-id")
    summary_command.add_argument("--parent-task-id")
    summary_command.add_argument("--role")
    summary_command.add_argument("--execution-status", choices=STATUSES)
    summary_command.add_argument("--json", action="store_true")
    for name in ("check", "run"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True, type=Path)
        command.add_argument("--local-config", type=Path)
        command.add_argument("--role", required=name == "run")
        if name == "run":
            command.add_argument("--task", required=True, help="Task text, or '-' to read from stdin")
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--task-id", help="Non-sensitive ID shared across attempts; otherwise generated")
            command.add_argument("--parent-task-id", help="Optional parent task ID from the task input")
            command.add_argument("--task-type", choices=sorted(TASK_TYPES))
            command.add_argument("--timeout-seconds", type=float, help="Optional elapsed-time limit; not a token cap")
            command.add_argument("--records-dir", type=Path, help="Opt-in private directory outside Git and project roots")
            command.add_argument("--result-ref", action="append", default=[], help="Explicit report/artifact reference; requires --records-dir")
            command.add_argument("--context-file", action="append", default=[], help="Explicit project text path or path:start:end to include once")
    args = parser.parse_args(argv)
    try:
        if args.command == "summary":
            validate_id(args.task_id, "task-id")
            validate_id(args.parent_task_id, "parent-task-id")
            directory = validate_records_dir(args.records_dir, [TEMPLATE_ROOT])
            result = summarize(directory, args.task_id, args.parent_task_id, args.role, args.execution_status)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print_summary(result)
            return 0 if result["scan_complete"] else 2
        config = load_config(TEMPLATE_ROOT, args.project, args.local_config)
        if args.command == "check":
            print(json.dumps(describe(config, args.role), ensure_ascii=False, indent=2))
            return 0
        task = sys.stdin.read() if args.task == "-" else args.task
        command, prompt, paths, prompt_stats = prepare_run(config, args.project, args.role, task, args.context_file)
        validate_id(args.task_id, "task-id")
        validate_id(args.parent_task_id, "parent-task-id")
        if args.parent_task_id is not None and args.parent_task_id == args.task_id:
            raise ConfigError("A task cannot be its own parent")
        if args.timeout_seconds is not None and (not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0):
            raise ConfigError("timeout-seconds must be a finite positive number")
        if args.result_ref and args.records_dir is None:
            raise ConfigError("--result-ref requires --records-dir")
        if any(not ref.strip() or len(ref) > 2048 for ref in args.result_ref):
            raise ConfigError("result-ref must be a non-empty reference of at most 2048 characters")
        records_dir = validate_records_dir(args.records_dir, [TEMPLATE_ROOT, args.project]) if args.records_dir is not None else None
        if args.dry_run:
            summary = describe(config, args.role)
            summary.update({"command": command, "loaded_files": [str(p) for p in paths], "prompt_characters": len(prompt)})
            summary["prompt_diagnostics"] = prompt_stats
            summary["execution"] = {"task_id": args.task_id, "parent_task_id": args.parent_task_id,
                                    "task_type": args.task_type, "timeout_seconds": args.timeout_seconds}
            summary["recording"] = {"enabled": records_dir is not None, "directory": str(records_dir) if records_dir else None}
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if shutil.which("codex") is None:
            raise ConfigError("Codex CLI is not installed or is not on PATH")
        # Preserve user/organization controls; do not bypass approvals or load auth ourselves.
        store = JsonRecordStore(records_dir, [TEMPLATE_ROOT, args.project]) if records_dir else None
        record = new_record(args.role, config["roles"][args.role], args.task_id, args.result_ref,
                            args.parent_task_id, args.task_type, args.timeout_seconds, prompt_stats)
        return execute_run(command, prompt, args.project, record, store)
    except (ValueError, OSError, UnicodeError) as exc:
        print(f"workflow: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
