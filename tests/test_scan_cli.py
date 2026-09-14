"""Scan imports and personal inventories through the public CLI."""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


class ScanCliTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "consumer"
        self.root.mkdir()
        self.repo = Path(__file__).resolve().parents[1]
        self.bin = self.base / "bin"
        self.bin.mkdir()
        scanner = self.bin / "skill-scanner"
        scanner.write_text(
            f"#!{sys.executable}\n"
            + (self.repo / "tests/fixtures/scan_engine.py").read_text()
        )
        scanner.chmod(0o755)
        (self.bin / "mode").write_text("clean")
        self.command = [sys.executable, "-B", str(self.repo / "python/skill_vendor.py")]
        self.report = self.base / "report.json"
        upstream = self.base / "upstream"
        skill = upstream / "skills/hello"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: hello\ndescription: Test skill.\n---\n"
        )
        (upstream / "LICENSE").write_text("Fixture licence")
        self.archive = self.base / "archive.tar.gz"
        with tarfile.open(self.archive, "w:gz") as archive:
            archive.add(upstream, arcname="source")
        self.spec = self.base / "source.json"
        self.spec.write_text(
            json.dumps(
                {
                    "name": "example",
                    "repository": "https://github.com/example/skills",
                    "revision": "a" * 40,
                    "update": {"kind": "branch", "ref": "main"},
                    "licenseFile": "LICENSE",
                    "skills": {"hello": {"path": "skills/hello"}},
                }
            )
        )

    def run_cli(self, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self.command, "--root", str(self.root), *map(str, args)],
            env={
                **os.environ,
                "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            },
            capture_output=True,
            text=True,
            check=False,
        )

    def import_skill(self, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return self.run_cli("--import", self.spec, "--archive", self.archive, *args)

    def test_github_import_scan_gates_publication(self) -> None:
        args = (
            "--from",
            "example/skills",
            "--skill",
            "hello",
            "--ref",
            "main",
            "--revision",
            "a" * 40,
            "--archive",
            self.archive,
            "--scan",
        )
        (self.bin / "mode").write_text("high")
        before = self.snapshot()
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.snapshot(), before)
        (self.bin / "mode").write_text("clean")
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "vendor/skills/hello/SKILL.md").is_file())

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def add_personal(self, body: str = "") -> None:
        directory = self.root / "local/personal"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: personal\ndescription: Personal test skill.\n---\n" + body
        )
        manifest = {
            "schemaVersion": 2,
            "sources": {},
            "localSkills": {"personal": {"path": "local/personal"}},
        }
        (self.root / "sources.json").write_text(json.dumps(manifest))

    def test_scanned_import_records_content_and_scanner(self) -> None:
        result = self.import_skill("--scan", "--scan-report", self.report)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.report.read_text())
        self.assertEqual(report["status"], "passed")
        self.assertIn("2.1.0", report["scanner"])
        self.assertEqual(report["sources"]["example"]["revision"], "a" * 40)
        self.assertEqual(len(report["skills"]["hello"]["contentSha256"]), 64)
        self.assertTrue((self.root / "vendor/example/hello/SKILL.md").exists())

    def test_findings_and_incomplete_scans_preserve_existing_import(self) -> None:
        self.assertEqual(self.import_skill().returncode, 0)
        before = self.snapshot()
        for mode in (
            "high",
            "incomplete",
            "missing",
            "malformed",
            "crash",
            "mutate",
            "unknown-severity",
            "wrong-skill",
            "warning",
        ):
            with self.subTest(mode=mode):
                (self.bin / "mode").write_text(mode)
                result = self.import_skill(
                    "--replace", "--scan", "--scan-report", self.base / f"{mode}.json"
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(self.snapshot(), before)
                report = json.loads((self.base / f"{mode}.json").read_text())
                self.assertEqual(
                    report["status"], "blocked" if mode == "high" else "incomplete"
                )
                if mode == "high":
                    self.assertEqual(set(report["skills"]), {"hello"})
                else:
                    self.assertIn("error", report)

    def test_scan_personal_inventory_without_writes(self) -> None:
        self.add_personal()
        before = self.snapshot()
        result = self.run_cli("--scan", "--scan-report", self.report)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.report.read_text())
        self.assertEqual(set(report["skills"]), {"personal"})
        self.assertEqual(self.snapshot(), before)

    def test_personal_finding_blocks_import(self) -> None:
        self.add_personal("BLOCK_SCAN")
        before = self.snapshot()
        result = self.import_skill("--scan", "--scan-report", self.report)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(json.loads(self.report.read_text())["status"], "blocked")

    def test_external_personal_inventory(self) -> None:
        extra = self.base / "extra.json"
        extra.write_text(
            json.dumps(
                {
                    "skills": {
                        "hello": {"path": str(self.base / "upstream/skills/hello")}
                    }
                }
            )
        )
        result = self.run_cli(
            "--scan", "--inventory", extra, "--scan-report", self.report
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "sources.json").exists())
        self.assertEqual(set(json.loads(self.report.read_text())["skills"]), {"hello"})

    def test_medium_findings_are_visible_and_do_not_block(self) -> None:
        (self.bin / "mode").write_text("medium")
        result = self.import_skill("--scan", "--scan-report", self.report)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MEDIUM", result.stdout)
        self.assertTrue(
            json.loads(self.report.read_text())["skills"]["hello"]["result"]["findings"]
        )

    def test_report_cannot_replace_consumer_files(self) -> None:
        self.add_personal()
        before = self.snapshot()
        result = self.run_cli("--scan", "--scan-report", self.root / "sources.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.snapshot(), before)

    def test_timeout_and_missing_scanner_stop_import(self) -> None:
        (self.bin / "mode").write_text("timeout")
        result = self.import_skill(
            "--scan", "--scan-timeout", "0.1", "--scan-report", self.report
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("timeout", result.stderr.lower())
        self.assertEqual(self.snapshot(), {})
        (self.bin / "skill-scanner").unlink()
        result = self.import_skill("--scan")
        self.assertEqual(result.returncode, 2)
        self.assertIn("with-scanner", result.stderr)
        self.assertEqual(self.snapshot(), {})

    def test_scanned_update_preserves_original_when_blocked(self) -> None:
        self.assertEqual(self.import_skill().returncode, 0)
        before = self.snapshot()
        script = """
import shutil, sys
from pathlib import Path
from skill_vendor import update
from skill_scan import ScanOptions
root, archive, report = map(Path, sys.argv[1:])
update(root, ['example'], resolve=lambda source: ('main', 'b' * 40),
       fetch=lambda source, target: shutil.copyfile(archive, target),
       scan=ScanOptions(report))
"""
        (self.bin / "mode").write_text("high")
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(self.root),
                str(self.archive),
                str(self.report),
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(self.repo / "python"),
                "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        (self.bin / "mode").write_text("clean")
        self.report.unlink()
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                script,
                str(self.root),
                str(self.archive),
                str(self.report),
            ],
            env={
                **os.environ,
                "PYTHONPATH": str(self.repo / "python"),
                "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads((self.root / "sources.json").read_text())["sources"]["example"][
                "revision"
            ],
            "b" * 40,
        )

    def test_scan_rejects_checks_and_invalid_timeout(self) -> None:
        for args in (
            ("--scan", "--check"),
            ("--scan", "--locked"),
            ("--scan", "--scan-timeout", "nan"),
            ("--scan", "--scan-timeout", "0"),
            ("--scan-report", str(self.report)),
        ):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(self.snapshot(), {})

    def test_empty_inventory_is_reported_explicitly(self) -> None:
        result = self.run_cli("--scan", "--scan-report", self.report)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.report.read_text())["skills"], {})
        self.assertIn("Scanned 0 declared skills", result.stdout)
        self.assertEqual(self.snapshot(), {})

    def test_concurrent_personal_edits_stop_import_and_are_preserved(self) -> None:
        self.add_personal()
        manifest = (self.root / "sources.json").read_bytes()
        (self.bin / "mode").write_text("concurrent")
        result = self.import_skill("--scan", "--scan-report", self.report)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("changed", result.stderr.lower())
        self.assertIn(
            "Concurrent edit", (self.root / "local/personal/SKILL.md").read_text()
        )
        self.assertEqual((self.root / "sources.json").read_bytes(), manifest)
        self.assertFalse((self.root / "vendor").exists())

    def test_scan_cannot_accept_changed_then_restored_personal_content(self) -> None:
        self.add_personal()
        last = self.root / "local/zlast"
        last.mkdir()
        (last / "SKILL.md").write_text(
            "---\nname: zlast\ndescription: Last test skill.\n---\n"
        )
        manifest_path = self.root / "sources.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["localSkills"]["zlast"] = {"path": "local/zlast"}
        manifest_path.write_text(json.dumps(manifest))
        (self.bin / "mode").write_text("swap")
        result = self.import_skill("--scan", "--scan-report", self.report)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((self.root / "vendor").exists())
