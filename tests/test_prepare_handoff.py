import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_handoff import MAX_TASK_BYTES, prepare, private_destination


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.task = self.project / "task.md"
        self.task.write_text("goal: 가상 인계\nacceptance: 확인\n", encoding="utf-8")
        self.artifact = self.project / "design.md"
        self.artifact.write_text("ARTIFACT_BODY_NOT_EMBEDDED", encoding="utf-8")
        self.output = self.root / "private" / "handoff.md"

    def run_prepare(self, **kwargs):
        arguments = dict(project=self.project, task="task.md", artifacts=["design.md"],
                         output=self.output, environment="orca")
        arguments.update(kwargs)
        return prepare(**arguments)

    def test_paths_hashes_permissions_and_no_artifact_body(self):
        output, manifest = self.run_prepare(artifacts=["design.md", str(self.artifact)])
        self.assertEqual(len(manifest["artifacts"]), 1)
        self.assertEqual(manifest["artifacts"][0]["path"], str(self.artifact))
        self.assertEqual(manifest["artifacts"][0]["sha256"],
                         hashlib.sha256(self.artifact.read_bytes()).hexdigest())
        self.assertIn(self.task.read_text(), output.read_text())
        self.assertNotIn("ARTIFACT_BODY_NOT_EMBEDDED", output.read_text())
        self.assertFalse(manifest["connection_verified"])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(output.parent.stat().st_mode), 0o700)

    def test_missing_input_fails_before_output_creation(self):
        with self.assertRaises(FileNotFoundError):
            self.run_prepare(artifacts=["missing.md"])
        self.assertFalse(self.output.parent.exists())

    def test_refuses_overwrite(self):
        self.run_prepare()
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            self.run_prepare()
        self.assertEqual(self.output.read_bytes(), original)

    def test_refuses_project_git_and_public_output(self):
        git_dir = self.root / "repository"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()
        public_dir = self.root / "public"
        public_dir.mkdir(mode=0o755)
        for output in [self.project / "handoff.md", git_dir / "out" / "handoff.md",
                       public_dir / "handoff.md"]:
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.run_prepare(output=output)
            self.assertFalse(output.exists())

    def test_refuses_output_symlinks(self):
        target = self.root / "target"
        target.mkdir(mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_prepare(output=alias / "handoff.md")
        self.assertFalse((target / "handoff.md").exists())

    def test_concurrent_parent_creation_revalidates_privacy_and_symlinks(self):
        original = Path.mkdir
        target = self.root / 'link-target'; target.mkdir(mode=0o700)
        for kind in ('private', 'public', 'symlink', 'file'):
            directory = self.root / ('racing-' + kind)
            def racing(path, *args, **kwargs):
                if path == directory:
                    if kind == 'symlink': path.symlink_to(target, target_is_directory=True)
                    elif kind == 'file': path.write_text('occupied')
                    else: original(path, mode=0o700 if kind == 'private' else 0o755)
                    raise FileExistsError('concurrent creator')
                return original(path, *args, **kwargs)
            with self.subTest(kind=kind), patch.object(Path, 'mkdir', racing):
                if kind == 'private':
                    self.assertEqual(private_destination(directory/'output.json', self.project), directory/'output.json')
                else:
                    with self.assertRaises(ValueError): private_destination(directory/'output.json', self.project)
            self.assertFalse((directory/'output.json').exists())

    def test_rejects_invalid_task_and_environment(self):
        for contents in [b" ", b"\xff", b"x" * (MAX_TASK_BYTES + 1)]:
            self.task.write_bytes(contents)
            with self.subTest(contents=len(contents)), self.assertRaises(ValueError):
                self.run_prepare()
        with self.assertRaises(ValueError):
            self.run_prepare(environment="auto")
        self.assertFalse(self.output.exists())

    def test_cli_failure_and_success_are_explicit(self):
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" /
                                      "prepare_handoff.py"), "--project", str(self.project),
                   "--environment", "paseo", "--task", "task.md", "--output", str(self.output)]
        failed = subprocess.run(command + ["--artifact", "missing.md"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        succeeded = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertFalse(json.loads(succeeded.stdout)["connection_verified"])


if __name__ == "__main__":
    unittest.main()
