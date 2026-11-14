import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import skill_catalog as skills


class SkillCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "skills").mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("/local/\n")
        self.spec = {"id": "sample", "repo": "example/skills", "path": "skills/sample", "ref": "a" * 40,
                     "roles": ["frontend"], "tags": ["web"], "mode": "reference-only",
                     "license": "MIT", "license_paths": [], "review": "reviewed original source"}
        self.write("skills/catalog.json", {"version": 1, "skills": [self.spec]})
        self.entry = self.make_entry(self.spec)
        self.state = {"version": 1, "skills": {"sample": self.entry}, "history": []}
        self.write("local/role-skills/registry.json", self.state)
        (self.root / "local/role-skills/registry.lock").touch()

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def make_entry(self, spec):
        relative = skills.location(spec, "pilot")
        folder = self.root / relative
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text("---\nname: sample\ndescription: Useful sample\n---\nVersion " + spec["ref"])
        return {"spec": copy.deepcopy(spec), "status": "pilot", "location": relative,
                "files": skills.inventory(folder)}

    def proof(self, spec=None):
        spec = spec or self.spec
        relative = "local/role-skills/runs/trial/evidence.json"
        artifact = "local/role-skills/runs/trial/result.md"
        path = self.root / artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Observed expected behavior on a bounded fixture")
        self.write(relative, {"subject": {k: spec[k] for k in ("id", "repo", "path", "ref", "roles", "tags", "mode")},
                              "source_review": True, "compatibility_review": True, "trial_passed": True,
                              "artifacts": [{"path": artifact, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}]})
        return relative

    def call(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = skills.main(["--root", str(self.root), *args])
        return code, output.getvalue()

    def test_selection_filters_role_stack_and_disabled_state(self):
        self.assertEqual(json.loads(self.call("select", "--role", "frontend", "--tag", "web")[1])["skills"][0]["id"], "sample")
        self.assertEqual(json.loads(self.call("select", "--role", "backend", "--tag", "web")[1])["skills"], [])
        self.assertEqual(json.loads(self.call("select", "--role", "frontend", "--tag", "expo")[1])["skills"], [])
        self.assertEqual(self.call("status", "--id", "sample", "--state", "disabled", "--reason", "incompatible")[0], 0)
        self.assertEqual(json.loads(self.call("select", "--role", "frontend", "--tag", "web")[1])["skills"], [])

    def test_readonly_selection_does_not_create_or_write_files(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        with patch.object(Path, "mkdir", side_effect=AssertionError("read-only must not mkdir")):
            self.assertEqual(self.call("select", "--role", "frontend", "--tag", "web")[0], 0)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_dirty_skill_is_preserved_and_retirement_fails(self):
        path = self.root / self.entry["location"] / "SKILL.md"
        path.write_text(path.read_text() + "\nUSER CHANGE")
        self.assertEqual(self.call("check")[0], 2)
        self.assertEqual(self.call("status", "--id", "sample", "--state", "disabled", "--reason", "retire")[0], 2)
        self.assertIn("USER CHANGE", path.read_text())

    def test_disable_retire_and_restore_preserve_original_hashes(self):
        self.assertEqual(self.call("status", "--id", "sample", "--state", "retired", "--reason", "unused")[0], 2)
        self.assertEqual(self.call("status", "--id", "sample", "--state", "disabled", "--reason", "replacement checked")[0], 0)
        self.assertEqual(self.call("status", "--id", "sample", "--state", "retired", "--reason", "no dependents")[0], 0)
        self.assertFalse((self.root / self.entry["location"]).exists())
        registry = skills.read_json(self.root / "local/role-skills/registry.json")
        self.assertEqual(skills.inventory(self.root / registry["skills"]["sample"]["location"]), self.entry["files"])
        self.assertEqual(self.call("status", "--id", "sample", "--state", "pilot", "--reason", "restore")[0], 0)
        self.assertEqual(skills.inventory(self.root / self.entry["location"]), self.entry["files"])

    def test_promotion_binds_trial_to_version_roles_and_artifacts(self):
        args = ("status", "--id", "sample", "--state", "active", "--reason", "trial passed")
        self.assertEqual(self.call(*args)[0], 2)
        proof = self.proof()
        wrong = copy.deepcopy(self.spec)
        wrong["ref"] = "b" * 40
        self.proof(wrong)
        self.assertEqual(self.call(*args, "--evidence", proof)[0], 2)
        self.proof()
        (self.root / "local/role-skills/runs/trial/result.md").write_text("modified")
        self.assertEqual(self.call(*args, "--evidence", proof)[0], 2)
        self.proof()
        self.assertEqual(self.call(*args, "--evidence", proof)[0], 0)

    def test_traversal_symlinks_and_unpinned_sources_are_rejected(self):
        bad = copy.deepcopy(self.spec)
        for key, value in (("path", "../outside"), ("ref", "main"), ("roles", ["typo"])):
            bad = {**self.spec, key: value}
            with self.assertRaises(ValueError):
                skills.validate_spec(bad)
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "local/role-skills/escape").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            skills.safe(self.root, "local/role-skills/escape/secret")

    def test_failed_registry_save_restores_moved_archive(self):
        self.entry["status"] = "disabled"
        original_location = self.entry["location"]
        with patch.object(skills, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                skills.change_status(self.root, self.state, "sample", "retired", "replacement checked")
        self.assertTrue((self.root / original_location).exists())
        self.assertEqual(self.state["skills"]["sample"]["status"], "disabled")

    def test_rollback_uses_verified_old_version_and_keeps_new_version(self):
        second = {**self.spec, "ref": "b" * 40}
        current = self.make_entry(second)
        self.state["skills"]["sample"] = current
        self.state["history"].append({"action": "install", "id": "sample", "previous": self.entry})
        skills.rollback(self.root, self.state, "sample", "regression")
        self.assertEqual(self.state["skills"]["sample"]["spec"]["ref"], "a" * 40)
        self.assertTrue((self.root / current["location"]).exists())

    def test_updating_seed_cannot_bypass_evidence_gate(self):
        updated = {**self.spec, "ref": "b" * 40}
        with patch.object(subprocess, "run", side_effect=AssertionError("must reject before installer")):
            with self.assertRaisesRegex(ValueError, "requires version-bound trial evidence"):
                skills.install(self.root, self.state, updated, Path("unused-installer"))
        self.assertEqual(self.state["skills"]["sample"]["spec"]["ref"], "a" * 40)

    def test_stale_review_cannot_overwrite_concurrent_registry_change(self):
        snapshot = skills.registry_hash(self.root)
        self.assertEqual(self.call("status", "--id", "sample", "--state", "disabled", "--reason", "user decision")[0], 0)
        code, _ = self.call("--expected-registry", snapshot, "status", "--id", "sample", "--state", "pilot", "--reason", "old review")
        self.assertEqual(code, 2)
        self.assertEqual(skills.load(self.root)[1]["skills"]["sample"]["status"], "disabled")

    def test_add_update_and_rollback_use_helper_and_preserve_both_versions(self):
        updated = {**self.spec, "ref": "b" * 40}
        self.write("candidate.json", updated)
        proof = self.proof(updated)
        helper = self.root / "reviewed_installer.py"
        helper.write_text(
            "import sys\nfrom pathlib import Path\n"
            "args=sys.argv\n"
            "dest=Path(args[args.index('--dest')+1])/args[args.index('--name')+1]\n"
            "dest.mkdir()\n"
            "(dest/'SKILL.md').write_text('---\\nname: sample\\ndescription: Updated fixture\\n---\\n')\n"
        )
        with patch.object(skills.urllib.request, "urlopen", return_value=io.BytesIO(b"MIT license source")):
            code, _ = self.call("--expected-registry", skills.registry_hash(self.root), "add", "--record", str(self.root / "candidate.json"),
                                "--evidence", proof, "--installer", str(helper))
        self.assertEqual(code, 0)
        catalog, state = skills.load(self.root)
        self.assertEqual(state["skills"]["sample"]["spec"]["ref"], "b" * 40)
        self.assertEqual(state["skills"]["sample"]["status"], "pilot")
        self.assertEqual(state["history"][-1]["previous"]["files"], self.entry["files"])
        self.assertEqual(self.call("check")[0], 0)
        self.assertEqual(self.call("rollback", "--id", "sample", "--reason", "behavior regressed")[0], 0)
        self.assertEqual(skills.load(self.root)[1]["skills"]["sample"]["spec"]["ref"], "a" * 40)
        self.assertTrue((self.root / skills.location(updated, "pilot")).exists())


if __name__ == "__main__":
    unittest.main()
