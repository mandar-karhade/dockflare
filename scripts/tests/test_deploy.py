"""Exercise deploy.sh against local Git repositories and fake remote commands.

Run: python3 -m unittest discover -s scripts/tests -v
No SSH, Docker, npm downloads, or real deployment is performed.
"""

# Subprocess arguments are controlled fixtures; PATH contains fake remote commands.
# ruff: noqa: S603, S607

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DEPLOY_SCRIPT = Path(__file__).resolve().parents[2] / "deploy.sh"

FAKE_COMMAND = r"""
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
entry = {"command": name, "args": args}
if name == "ssh" and "bash" in args and "-s" in args:
    entry["script"] = sys.stdin.read()
if name == "ssh" and any("printf %s" in arg for arg in args):
    print("/home/deployer")
if name == "rsync":
    source = Path(args[-2])
    entry["source"] = str(source)
    entry["marker"] = (source / "backend/app/branch.txt").read_text()
    entry["manifest"] = json.loads((source / "frontend/package.json").read_text())
    entry["lock"] = json.loads((source / "frontend/package-lock.json").read_text())
    entry["files"] = sorted(str(p.relative_to(source)) for p in source.rglob("*"))
if name == "scp":
    entry["config"] = Path(args[0]).read_text()
if name == "npm":
    entry["cwd"] = str(Path.cwd())
    manifest = json.loads(Path("package.json").read_text())
    Path("package-lock.json").write_text(json.dumps({"name": manifest["name"]}))
with open(os.environ["DEPLOY_TEST_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\n")
if os.environ.get("DEPLOY_TEST_FAIL") == name:
    sys.exit(1)
"""


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="dockflare-deploy-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.repo = self.root / "working copy"
        self.log = self.root / "commands.jsonl"
        self.scratch = self.root / "temporary snapshots"
        self.scratch.mkdir()
        self.git(self.root, "init", "-q", "--initial-branch=feature/seed", str(self.seed))
        self.git(self.seed, "config", "user.name", "Deploy Test")
        self.git(self.seed, "config", "user.email", "deploy-test@example.invalid")
        for folder in ("backend/app", "frontend", "docker"):
            (self.seed / folder).mkdir(parents=True)
        (self.seed / ".gitignore").write_text(".env\npackage-lock.json\n")
        (self.seed / "docker/compose.yml").write_text("services: {}\n")
        self.set_marker("main")
        self.commit("Main fixture")
        self.git(self.seed, "branch", "main")
        self.git(self.seed, "switch", "-c", "feature/dev-seed")
        self.set_marker("dev")
        self.commit("Dev fixture")
        self.git(self.seed, "branch", "dev")
        self.git(self.root, "clone", "--bare", str(self.seed), str(self.origin))
        self.git(self.seed, "remote", "add", "origin", str(self.origin))
        self.git(self.root, "clone", "-b", "dev", str(self.origin), str(self.repo))
        self.git(self.repo, "switch", "-c", "feature/local-work")
        shutil.copy2(DEPLOY_SCRIPT, self.repo / "deploy.sh")
        (self.repo / ".env").write_text("CF_TOKEN=test-only-token\n")
        (self.repo / "backend/app/branch.txt").write_text("uncommitted local edit")
        (self.repo / "local-only.txt").write_text("must not deploy")
        (self.repo / "frontend/package-lock.json").write_text('{"name":"wrong-local-lock"}')
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("ssh", "rsync", "scp", "npm"):
            executable = self.bin / name
            executable.write_text(f"#!{sys.executable}\n" + FAKE_COMMAND)
            executable.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(self.scratch),
            "DEPLOY_TEST_LOG": str(self.log),
            "DOCKFLARE_HOST": "deployer@example.invalid",
            "DOCKFLARE_APP_DIR": "",
        }

    def git(self, cwd, *args):
        return subprocess.check_output(
            ["git", *args], cwd=cwd, text=True, stderr=subprocess.PIPE
        ).strip()

    def set_marker(self, marker):
        (self.seed / "backend/app/branch.txt").write_text(marker)
        (self.seed / "frontend/package.json").write_text(
            json.dumps({"name": marker, "version": "0.1.0"})
        )

    def commit(self, message):
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-qm", message)

    def run_deploy(self, *args, reply="y", fail=None):
        before = self.git(self.repo, "status", "--porcelain")
        self.log.unlink(missing_ok=True)
        result = subprocess.run(
            ["bash", str(self.repo / "deploy.sh"), *args],
            cwd=self.root,
            input=reply,
            capture_output=True,
            text=True,
            env={**self.env, "DEPLOY_TEST_FAIL": fail or ""},
            timeout=30,
        )
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), before)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "feature/local-work")
        self.assertEqual(list(self.scratch.iterdir()), [], "temporary sources leaked")
        calls = (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists()
            else []
        )
        return result, calls

    def test_main_default_and_explicit_ignore_dirty_working_tree(self):
        for flags in ([], ["--main"]):
            with self.subTest(flags=flags):
                result, calls = self.run_deploy(*flags, "--sync-only")
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = next(c for c in calls if c["command"] == "rsync")
                self.assertEqual(payload["marker"], "main")
                self.assertEqual(payload["lock"]["name"], "main")
                self.assertNotIn("local-only.txt", payload["files"])
                self.assertNotIn(".env", payload["files"])
                self.assertIn("Branch: origin/main", result.stdout)
                self.assertIn(self.git(self.seed, "rev-parse", "main"), result.stdout)
                self.assertIn(
                    "Host: deployer@example.invalid:/home/deployer/dockflare", result.stdout
                )
                self.assertEqual(
                    next(c for c in calls if c["command"] == "scp")["config"],
                    "CF_TOKEN=test-only-token\n",
                )
                self.assertFalse(any("script" in c for c in calls))
                npm = next(c for c in calls if c["command"] == "npm")
                self.assertIn("--package-lock-only", npm["args"])
                self.assertIn("--ignore-scripts", npm["args"])

    def test_dev_fetches_remote_update_and_preserves_component_and_destination(self):
        self.set_marker("new-dev")
        self.commit("New remote dev commit")
        self.git(self.seed, "push", "origin", "HEAD:dev")
        result, calls = self.run_deploy(
            "--dev", "--host", "m@192.168.0.161", "--app-dir", "/srv/dockflare", "frontend"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(next(c for c in calls if c["command"] == "rsync")["marker"], "new-dev")
        build = next(c for c in calls if "script" in c)
        self.assertEqual(
            build["args"], ["m@192.168.0.161", "bash", "-s", "/srv/dockflare", "frontend"]
        )
        self.assertIn('$COMPOSE build "$COMPONENT"', build["script"])
        self.assertIn(self.git(self.seed, "rev-parse", "HEAD"), result.stdout)

    def test_full_deploy_uses_all_services(self):
        result, calls = self.run_deploy("--dev")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(next(c for c in calls if "script" in c)["args"][-1], "all")

    def test_conflicting_flags_fail_before_remote_access(self):
        for flags in (("--dev", "--main"), ("--main", "--dev")):
            result, calls = self.run_deploy(*flags)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot be used together", result.stderr)
            self.assertEqual(calls, [])

    def test_fetch_failure_does_not_deploy_stale_tracking_branch(self):
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "missing-origin"))
        result, calls = self.run_deploy("--dev")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not fetch origin/dev", result.stderr)
        self.assertEqual(calls, [])

    def test_cancel_and_eof_do_not_mutate_remote(self):
        for reply in ("n", ""):
            result, calls = self.run_deploy("--dev", reply=reply)
            self.assertIn("Aborted", result.stdout)
            self.assertFalse(any(c["command"] in ("rsync", "scp") for c in calls))
            self.assertFalse(any("mkdir" in " ".join(c["args"]) for c in calls))

    def test_lockfile_failure_aborts_before_ssh(self):
        result, calls = self.run_deploy("--dev", fail="npm")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([c["command"] for c in calls], ["npm"])

    def test_transfer_failure_cleans_snapshot_and_skips_build(self):
        result, calls = self.run_deploy("--dev", fail="rsync")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["command"] == "scp" or "script" in c for c in calls))

    def test_committed_lockfile_used_without_npm(self):
        (self.seed / "frontend/package-lock.json").write_text('{"name":"committed-lock"}')
        self.git(self.seed, "add", "-f", "frontend/package-lock.json")
        self.commit("Commit lockfile")
        self.git(self.seed, "push", "origin", "HEAD:dev")
        result, calls = self.run_deploy("--dev", "--sync-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c["command"] == "npm" for c in calls))
        self.assertEqual(
            next(c for c in calls if c["command"] == "rsync")["lock"]["name"], "committed-lock"
        )

    def test_setup_does_not_require_env_or_fetch(self):
        (self.repo / ".env").unlink()
        self.git(self.repo, "remote", "set-url", "origin", str(self.root / "missing-origin"))
        result, calls = self.run_deploy("--dev", "--setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(calls)
        self.assertTrue(all(c["command"] == "ssh" for c in calls))


if __name__ == "__main__":
    unittest.main()
