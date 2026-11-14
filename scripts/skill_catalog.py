#!/usr/bin/env python3
"""Pinned, role-scoped skill library. No downloaded skill code is executed."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
ROLES = {"coordinator", "architect", "designer", "frontend", "backend", "verifier"}
STATES = {"pilot", "active", "disabled", "retired"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def registry_hash(root):
    path = state_path(root)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def safe(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Expected a non-empty relative path")
    path = root / relative
    if ".." in Path(relative).parts:
        raise ValueError("Parent traversal is not allowed")
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symbolic link is not managed: {relative}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escaped its managed root")
    return path


def validate_spec(spec):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", spec.get("id", "")):
        raise ValueError("Invalid skill id")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", spec.get("repo", "")):
        raise ValueError("Expected GitHub owner/repository")
    if not re.fullmatch(r"[a-f0-9]{40}", spec.get("ref", "")):
        raise ValueError("Source must be pinned to a full commit SHA")
    safe(Path("/unused"), spec.get("path"))
    roles, tags = spec.get("roles"), spec.get("tags")
    if not isinstance(roles, list) or not roles or not set(roles) <= ROLES:
        raise ValueError("Invalid or empty role mapping")
    if not isinstance(tags, list) or not tags or not all(isinstance(t, str) and t for t in tags):
        raise ValueError("Invalid skill tags")
    if spec.get("mode") not in {"skill", "reference-only"}:
        raise ValueError("Unknown invocation mode")
    if not spec.get("license") or not spec.get("review"):
        raise ValueError("License and source review are required")
    if not isinstance(spec.get("license_paths"), list):
        raise ValueError("license_paths must be a list")
    for path in spec["license_paths"]:
        safe(Path("/unused"), path)


def inventory(folder):
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError(f"Missing managed skill: {folder}")
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError("Unsupported entry in skill library")
        if path.is_file():
            result[path.relative_to(folder).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n") or not re.search(r"^name:\s*\S", text, re.M) or not re.search(r"^description:\s*\S", text, re.M):
        raise ValueError("Invalid skill frontmatter")
    if not result:
        raise ValueError("Empty skill library")
    return result


def state_path(root):
    # Reject symlinks even in the ignored storage's ancestor directories.
    return safe(root, "local/role-skills/registry.json")


def load(root):
    catalog = read_json(root / "skills/catalog.json")
    if catalog.get("version") != 1:
        raise ValueError("Unsupported catalog version")
    ids = set()
    for spec in catalog["skills"]:
        validate_spec(spec)
        if spec["id"] in ids:
            raise ValueError("Duplicate seed skill")
        ids.add(spec["id"])
    path = state_path(root)
    state = read_json(path) if path.exists() else {"version": 1, "skills": {}, "history": []}
    if state.get("version") != 1:
        raise ValueError("Unsupported registry version")
    for key, entry in state["skills"].items():
        validate_spec(entry["spec"])
        if entry["spec"]["id"] != key or entry["status"] not in STATES:
            raise ValueError("Invalid registry entry")
        expected = location(entry["spec"], entry["status"])
        if entry["location"] != expected or not isinstance(entry["files"], dict):
            raise ValueError("Registry location or integrity inventory is invalid")
    return catalog, state


def location(spec, status):
    area = "archive" if status == "retired" else "library"
    return f"local/role-skills/{area}/{spec['id']}/{spec['ref']}"


def verify_entry(root, entry):
    path = safe(root, entry["location"])
    if inventory(path) != entry["files"]:
        raise ValueError(f"Locally modified skill; preserve and review: {entry['spec']['id']}")
    return path


@contextlib.contextmanager
def locked(root, write=True):
    if not write:
        lock = safe(root, "local/role-skills/registry.lock")
        if not lock.exists():
            yield
            return
        with lock.open("r") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        return
    local = safe(root, "local/role-skills")
    tracked = subprocess.run(["git", "ls-files", "--", "local/role-skills"], cwd=root, capture_output=True, text=True, check=True)
    ignored = subprocess.run(["git", "check-ignore", "-q", "local/role-skills/registry.json"], cwd=root)
    if tracked.stdout or ignored.returncode != 0:
        raise ValueError("Managed local storage must be untracked and Git-ignored")
    local.mkdir(parents=True, exist_ok=True)
    lock = safe(root, "local/role-skills/registry.lock")
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def save(root, state, action, skill_id, reason, previous=None):
    state["history"].append({"at": now(), "action": action, "id": skill_id, "reason": reason,
                             "previous": previous})
    path = state_path(root)
    temporary = path.with_suffix(".tmp")
    safe(root, temporary.relative_to(root).as_posix())
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def evidence(root, relative, spec):
    if not relative or not relative.startswith("local/role-skills/runs/"):
        raise ValueError("Review evidence must be in a managed run directory")
    proof = read_json(safe(root, relative))
    binding = {key: spec[key] for key in ("id", "repo", "path", "ref", "roles", "tags", "mode")}
    if proof.get("subject") != binding:
        raise ValueError("Evidence does not match this source version and role mapping")
    if not all(proof.get(k) is True for k in ("source_review", "compatibility_review", "trial_passed")):
        raise ValueError("Source, compatibility and behavioral trial must pass")
    if not proof.get("artifacts"):
        raise ValueError("Behavioral evidence artifacts are required")
    for item in proof["artifacts"]:
        if not item["path"].startswith("local/role-skills/runs/"):
            raise ValueError("Evidence artifact escaped managed runs")
        path = safe(root, item["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Evidence artifact changed")
    return proof


def install(root, state, spec, installer, proof=None):
    validate_spec(spec)
    old = state["skills"].get(spec["id"])
    if old:
        verify_entry(root, old)
        if old["spec"]["ref"] == spec["ref"]:
            if old["spec"] != spec:
                raise ValueError("Same source ref with changed mapping: review explicitly")
            return "already-installed"
        if not proof:
            raise ValueError("Updating an installed seed requires version-bound trial evidence; use add")
    if proof:
        evidence(root, proof, spec)
    dest = safe(root, location(spec, "pilot"))
    if dest.exists():
        raise ValueError("Unregistered destination already exists; preserve it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # The caller supplies the already-reviewed installer helper, never downloaded code.
    with tempfile.TemporaryDirectory(prefix="skill-install-") as temp:
        command = [sys.executable, str(installer), "--repo", spec["repo"], "--path", spec["path"],
                   "--ref", spec["ref"], "--dest", temp, "--name", spec["id"], "--method", "download"]
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
        source = Path(temp) / spec["id"]
        legal = source / "upstream-legal"
        legal.mkdir(exist_ok=True)
        for index, item in enumerate(spec["license_paths"]):
            url = f"https://raw.githubusercontent.com/{spec['repo']}/{spec['ref']}/{item}"
            with urllib.request.urlopen(url, timeout=30) as response:
                (legal / f"{index}-{Path(item).name}").write_bytes(response.read())
        if not spec["license_paths"]:
            # Vercel declares MIT in its README; preserve that upstream declaration.
            url = f"https://raw.githubusercontent.com/{spec['repo']}/{spec['ref']}/README.md"
            with urllib.request.urlopen(url, timeout=30) as response:
                (legal / "LICENSE-SOURCE-README.md").write_bytes(response.read())
        files = inventory(source)
        shutil.copytree(source, dest)
    entry = {"spec": spec, "status": "pilot", "location": location(spec, "pilot"), "files": files,
             "installed_at": now(), "evidence": proof}
    # Keep old immutable versions in place for rollback; never overwrite them.
    state["skills"][spec["id"]] = entry
    try:
        save(root, state, "install", spec["id"], "source-reviewed pilot" if not proof else proof, old)
    except Exception:
        # An orphan version is preserved, not deleted; check will still show the old state.
        raise
    return "installed-pilot"


def change_status(root, state, skill_id, status, reason, proof=None):
    if not reason.strip():
        raise ValueError("A review reason is required")
    entry = state["skills"][skill_id]
    source = verify_entry(root, entry)
    if status == "active":
        evidence(root, proof, entry["spec"])
    if status == "retired" and entry["status"] != "disabled":
        raise ValueError("Disable and assess dependencies/replacement before retirement")
    target = safe(root, location(entry["spec"], status))
    if target != source:
        if target.exists():
            raise ValueError("Archive/restore destination already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
    previous = dict(entry)
    entry.update(status=status, location=target.relative_to(root).as_posix())
    if proof:
        entry["evidence"] = proof
    try:
        save(root, state, status, skill_id, reason, previous)
    except Exception:
        state["skills"][skill_id] = previous
        if target != source:
            target.rename(source)
        raise


def rollback(root, state, skill_id, reason):
    current = state["skills"][skill_id]
    verify_entry(root, current)
    prior = next((item["previous"] for item in reversed(state["history"])
                  if item["id"] == skill_id and item["action"] in {"install", "rollback"}
                  and item.get("previous")), None)
    if not prior:
        raise ValueError("No previous managed version to restore")
    verify_entry(root, prior)
    state["skills"][skill_id] = prior
    save(root, state, "rollback", skill_id, reason, current)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--expected-registry", help="SHA-256 observed before review; reject concurrent changes")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    selection = sub.add_parser("select")
    selection.add_argument("--role", choices=sorted(ROLES), required=True)
    selection.add_argument("--tag", action="append", default=[])
    bootstrap = sub.add_parser("install")
    bootstrap.add_argument("--installer", type=Path, required=True)
    bootstrap.add_argument("--id", action="append", default=[])
    add = sub.add_parser("add")
    add.add_argument("--record", type=Path, required=True)
    add.add_argument("--evidence", required=True)
    add.add_argument("--installer", type=Path, required=True)
    status = sub.add_parser("status")
    status.add_argument("--id", required=True)
    status.add_argument("--state", choices=sorted(STATES), required=True)
    status.add_argument("--reason", required=True)
    status.add_argument("--evidence")
    revert = sub.add_parser("rollback")
    revert.add_argument("--id", required=True)
    revert.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        with locked(root, write=args.command in {"install", "add", "status", "rollback"}):
            if args.command == "add" and not args.expected_registry:
                raise ValueError("add requires --expected-registry from the reviewed registry snapshot")
            if args.expected_registry and registry_hash(root) != args.expected_registry:
                raise ValueError("Registry changed after review; preserve current state and re-evaluate")
            catalog, state = load(root)
            if args.command == "install":
                unknown = set(args.id) - {s["id"] for s in catalog["skills"]}
                if unknown:
                    raise ValueError(f"Unknown seed skills: {sorted(unknown)}")
                for spec in catalog["skills"]:
                    if not args.id or spec["id"] in args.id:
                        print(f"{spec['id']}: {install(root, state, spec, args.installer)}", flush=True)
            elif args.command == "add":
                spec = read_json(args.record)
                print(install(root, state, spec, args.installer, args.evidence))
            elif args.command == "status":
                change_status(root, state, args.id, args.state, args.reason, args.evidence)
                print(args.state)
            elif args.command == "rollback":
                rollback(root, state, args.id, args.reason)
                print("restored-previous-version")
            else:
                rows = []
                for entry in state["skills"].values():
                    spec = entry["spec"]
                    if args.command == "select":
                        if args.role not in spec["roles"] or entry["status"] not in {"active", "pilot"}:
                            continue
                        if "common" not in spec["tags"] and not set(args.tag) & set(spec["tags"]):
                            continue
                    path = verify_entry(root, entry)
                    rows.append({"id": spec["id"], "status": entry["status"], "mode": spec["mode"],
                                 "entrypoint": str(path / "SKILL.md"), "roles": spec["roles"], "tags": spec["tags"]})
                missing = [s["id"] for s in catalog["skills"] if s["id"] not in state["skills"]]
                print(json.dumps({"skills": rows, "not_installed": missing}, ensure_ascii=False, indent=2))
                if args.command == "check" and missing:
                    return 1
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(f"skill-catalog: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr[-2000:], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
