#!/usr/bin/env python3
"""Select a native environment guide from session evidence and read-only probes."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS = ("auto", "orca", "paseo")


class StartupError(ValueError):
    pass


def reject_constant(value):
    raise StartupError("Non-finite JSON value")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StartupError("Duplicate configuration key")
        result[key] = value
    return result


def keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise StartupError(f"{label}: expected an object with supported fields")


def relative_path(root, value, *, directory=False):
    if not isinstance(value, str) or not value.strip() or Path(value).is_absolute():
        raise StartupError("Project paths must be nonempty relative paths")
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise StartupError("Project path escapes its root")
    if not (path.is_dir() if directory else path.is_file()):
        raise StartupError("Project path does not exist")
    if not directory and path.suffix.lower() != ".md":
        raise StartupError("Project instructions must be Markdown files")
    return path


def instructions(root, values):
    if not isinstance(values, list):
        raise StartupError("project_instructions must be an array")
    return list(dict.fromkeys(str(relative_path(root, value)) for value in values))


def load_project(project, config_path=None):
    project = Path(project).resolve()
    if not project.is_dir():
        raise StartupError("Project directory does not exist")
    explicit_config = config_path is not None
    config_path = Path(config_path) if explicit_config else project / ".ai-workflow-startup.json"
    if config_path.is_symlink():
        raise StartupError("Startup config must not be a symlink")
    config_path = config_path.resolve()
    exists = config_path.exists()
    data = {}
    if exists:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys,
                              parse_constant=reject_constant)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StartupError("Cannot read startup configuration") from exc
        keys(data, ("version", "environment", "agent", "project_instructions", "repositories"), "startup config")
        if type(data.get("version")) is not int or data["version"] != 1:
            raise StartupError("Startup config version must be 1")
    elif explicit_config:
        raise StartupError("Explicit startup config does not exist")
    environment = data.get("environment", "auto")
    if not isinstance(environment, str) or environment not in ENVIRONMENTS:
        raise StartupError("Unsupported environment")
    agent = data.get("agent", {"provider": None, "model": None})
    keys(agent, ("provider", "model"), "agent")
    for value in agent.values():
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise StartupError("Agent provider/model must be a nonempty string or null")
    project_guides = data.get("project_instructions", ["AGENTS.md"] if (project / "AGENTS.md").is_file() else [])
    repositories = data.get("repositories", {})
    if not isinstance(repositories, dict):
        raise StartupError("repositories must be an object")
    targets = {}
    seen_paths = set()
    for name, entry in repositories.items():
        if not name or not name.strip():
            raise StartupError("Repository names must not be empty")
        keys(entry, ("path", "project_instructions"), "repository")
        if "path" not in entry:
            raise StartupError("Repository path is required")
        path = relative_path(project, entry["path"], directory=True)
        if path == project or path in seen_paths:
            raise StartupError("Repositories must have distinct child paths")
        seen_paths.add(path)
        guides = entry.get("project_instructions", ["AGENTS.md"] if (path / "AGENTS.md").is_file() else [])
        targets[name] = {"path": str(path), "project_instructions": instructions(path, guides)}
    return {"project": str(project), "config": str(config_path) if exists else None,
            "environment": environment, "agent_request": {"provider": agent.get("provider"), "model": agent.get("model")},
            "project_instructions": instructions(project, project_guides), "repositories": targets}


def select_environment(explicit, configured, environ):
    choice = explicit if explicit is not None else configured
    if choice not in ENVIRONMENTS:
        raise StartupError("Unsupported environment")
    if choice != "auto":
        return choice, "argument" if explicit is not None else "config"
    candidates = []
    if environ.get("ORCA_AGENT_SESSION_ID"):
        candidates.append("orca")
    if environ.get("PASEO_AGENT_ID"):
        candidates.append("paseo")
    if len(candidates) != 1:
        raise StartupError("Session environment is missing or ambiguous; specify --environment or startup config")
    return candidates[0], "session"


def executable_for(environment, environ):
    if environment == "orca":
        # Match the installed Orca discovery guide; do not silently try other binaries.
        if environ.get("ORCA_CLI_COMMAND"):
            command = environ["ORCA_CLI_COMMAND"]
        elif environ.get("ORCA_DEV_REPO_ROOT"):
            command = "orca-dev"
        else:
            command = "orca-ide" if sys.platform.startswith("linux") else "orca"
    else:
        command = "paseo"
    if not command.strip() or (not Path(command).is_file() and shutil.which(command) is None):
        raise StartupError(f"{environment}: CLI unavailable; no fallback")
    return command


def command_json(argv, project, timeout):
    try:
        result = subprocess.run(argv, cwd=project, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StartupError("Environment status probe failed or timed out; no fallback") from exc
    if result.returncode != 0:
        # Do not expose raw error bodies: they may contain endpoints or credentials.
        raise StartupError("Environment status probe was denied or failed; use the normal approval process; no fallback")
    try:
        data = json.loads(result.stdout, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    except (ValueError, TypeError) as exc:
        raise StartupError("Environment returned invalid status JSON; no fallback") from exc
    if not isinstance(data, dict):
        raise StartupError("Environment status must be an object")
    return data


def probe_environment(environment, source, environ, project, timeout):
    cli = executable_for(environment, environ)
    status = command_json([cli, "status", "--json"], project, timeout)
    if environment == "orca":
        result = status.get("result", {})
        if status.get("ok") is not True or not isinstance(result, dict):
            raise StartupError("Orca did not confirm connection")
        runtime = result.get("runtime", {})
        if not isinstance(runtime, dict) or runtime.get("state") != "ready" or runtime.get("connectionState") != "connected" or runtime.get("reachable") is not True:
            raise StartupError("Orca runtime is not ready and connected")
        caller = result.get("caller", {})
        session = environ.get("ORCA_AGENT_SESSION_ID", "")
        expected = session if session.startswith("orca_session_id:") else "orca_session_id:" + session
        verified = (bool(session) and isinstance(caller, dict) and caller.get("live") is True
                    and caller.get("orcaSessionId") == expected)
        if source == "session" and not verified:
            raise StartupError("Orca caller does not confirm the session identity; choose explicit environment after checking it")
        return {"status": "connected", "version": runtime.get("appVersion"),
                "session_identity_confirmed": verified, "command": cli}
    if status.get("connectedDaemon") != "reachable" or not isinstance(status.get("serverId"), str) or not status["serverId"]:
        raise StartupError("Paseo daemon did not confirm connection")
    # The documented PASEO_AGENT_ID marks the origin, but daemon status alone does
    # not verify that this exact agent is still live. The native guide requires that check.
    return {"status": "connected", "version": status.get("daemonVersion"),
            "session_identity_confirmed": False, "command": cli}


def startup(project, explicit=None, config_path=None, timeout=8):
    if not math.isfinite(timeout) or timeout <= 0:
        raise StartupError("Probe timeout must be finite and positive")
    loaded = load_project(project, config_path)
    environment, source = select_environment(explicit, loaded["environment"], os.environ)
    connection = probe_environment(environment, source, os.environ, loaded["project"], timeout)
    guide = ROOT / "runtime" / "environments" / (environment + ".md")
    common = [ROOT / "AGENTS.md", ROOT / "roles" / "README.md", ROOT / "templates" / "README.md"]
    for path in [guide, *common]:
        if not path.is_file():
            raise StartupError("Required common/environment guide missing")
    return {"status": "guide_selected", "environment": environment, "selection_source": source,
            "connection": connection, "common_guides": [str(path) for path in common],
            "environment_guide": str(guide), **{k: v for k, v in loaded.items() if k != "environment"},
            "scope": "Guide selection and status checks only; no agent launch, task dispatch or model call",
            "capabilities": "Native lifecycle/model/permission support must be checked using the selected guide",
            "project_scope_confirmed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--environment", choices=ENVIRONMENTS)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--probe-timeout", type=float, default=8)
    args = parser.parse_args(argv)
    try:
        result = startup(args.project, args.environment, args.config, args.probe_timeout)
    except (OSError, ValueError, TypeError) as exc:
        print(f"startup: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
