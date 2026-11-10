#!/usr/bin/env python3
"""Prepare explicit local references for a handoff; never launch an agent."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from run_records import validate_records_dir


TEMPLATE_ROOT = Path(__file__).resolve().parents[1]
MAX_TASK_BYTES = 64 * 1024


def reference(project, value):
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else project / path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Input must be a regular file: {path}")
    return path


def describe(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Input changed while hashing: {path}")
    return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}


def private_destination(output, project):
    output = Path(output).expanduser().absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("Output must not use symlinks")
    directory = validate_records_dir(output.parent, [TEMPLATE_ROOT, project])
    missing = []
    cursor = directory
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for path in reversed(missing):
        path.mkdir(mode=0o700)
    validate_records_dir(directory, [TEMPLATE_ROOT, project])
    return directory / output.name


def prepare(project, task, artifacts, output, environment):
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError("Project must be a directory")
    if environment not in {"orca", "paseo"}:
        raise ValueError("Choose an explicit environment: orca or paseo")
    task_path = reference(project, task)
    with task_path.open("rb") as stream:
        task_bytes = stream.read(MAX_TASK_BYTES + 1)
    if len(task_bytes) > MAX_TASK_BYTES or not task_bytes.strip():
        raise ValueError("Task must be nonempty UTF-8 text of at most 64 KiB")
    task_text = task_bytes.decode("utf-8")
    paths = list(dict.fromkeys(reference(project, item) for item in artifacts))
    manifest = {
        "schema_version": 1,
        "environment": environment,
        "project_root": str(project),
        "connection_verified": False,
        "task": {"path": str(task_path), "bytes": len(task_bytes),
                 "sha256": hashlib.sha256(task_bytes).hexdigest()},
        "artifacts": [describe(path) for path in paths],
    }
    # Validate every input before creating the private destination.
    destination = private_destination(output, project)
    text = (
        "# Prepared handoff\n\n"
        "Local file snapshot only. The receiving worker must confirm its host, "
        "workspace and access. Permissions, connection and task acceptance have "
        "not been verified by this tool. Artifact contents are not embedded.\n\n"
        "```json\n" + json.dumps(manifest, ensure_ascii=False, indent=2) + "\n```\n\n"
        "## Task\n\n" + task_text.rstrip() + "\n\n"
        "## Return location\n\n"
        "Use the receiving worker's permitted output directory and return absolute "
        "artifact paths. Do not assume another worker's scratch is your scratch.\n"
    )
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
    except BaseException:
        destination.unlink()
        raise
    return destination, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--environment", required=True, choices=["orca", "paseo"])
    parser.add_argument("--task", required=True)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        path, manifest = prepare(args.project, args.task, args.artifact,
                                 args.output, args.environment)
    except (OSError, ValueError) as error:
        print(f"Handoff preparation failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(path), "artifact_count": len(manifest["artifacts"]),
                      "connection_verified": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
