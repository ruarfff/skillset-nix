"""Exercise the public CLI without network access or installed agent state."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import skill_vendor
from skill_inventory import Skill


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.repo = Path(__file__).resolve().parents[1]
        self.root = self.base / "consumer"
        self.root.mkdir()
        (self.root / "local-note").write_text("Preserve this file.")
        upstream = self.base / "upstream"
        for name in ("hello", "helper"):
            skill = upstream / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Synthetic CLI fixture.\n---\n"
            )
            (skill / "run.sh").write_text("#!/bin/sh\nexit 0\n")
            (skill / "run.sh").chmod(0o755)
        (upstream / "LICENSE").write_text("Synthetic licence\n")
        (upstream / "AGENTS.md").symlink_to("CLAUDE.md")
        self.archive = self.base / "fixture.tar.gz"
        with tarfile.open(self.archive, "w:gz") as output:
            output.add(upstream, arcname="upstream")
        self.spec = {
            "name": "fixture",
            "repository": "https://github.com/example/fixture",
            "revision": "a" * 40,
            "update": {"kind": "branch", "ref": "main"},
            "licenseFile": "LICENSE",
            "license": "MIT",
            "skills": {
                name: {"path": f"skills/{name}"} for name in ("hello", "helper")
            },
        }
        self.spec_path = self.base / "source.json"
        self.save_spec()
        self.library = self.repo / "python"
        self.command = [
            sys.executable,
            "-B",
            str(self.repo / "python/skill_vendor.py"),
        ]

    def save_spec(self) -> None:
        self.spec_path.write_text(json.dumps(self.spec))

    def run_cli(self, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command + ["--root", str(self.root), *map(str, args)],
            capture_output=True,
            text=True,
            check=False,
        )

    def onboard(self, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "--import", self.spec_path, "--archive", self.archive, *args
        )

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def from_github(self, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "--from",
            "example/fixture",
            "--ref",
            "main",
            "--revision",
            "a" * 40,
            "--archive",
            self.archive,
            *args,
        )

    def repack(self) -> None:
        with tarfile.open(self.archive, "w:gz") as output:
            output.add(self.base / "upstream", arcname="upstream")

    def test_github_import_without_json(self) -> None:
        self.spec_path.unlink()
        result = self.from_github("--skill", "hello", "--skill", "helper")
        self.assertEqual(result.returncode, 0, result.stderr)
        source = json.loads((self.root / "sources.json").read_text())["sources"][
            "fixture"
        ]
        self.assertEqual(source["skills"], self.spec["skills"])
        self.assertEqual(source["revision"], "a" * 40)
        self.assertEqual(source["repository"], self.spec["repository"])
        self.assertEqual(
            source["archiveSha256"],
            hashlib.sha256(self.archive.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            (self.root / "vendor/fixture/hello/LICENSE").read_bytes(),
            b"Synthetic licence\n",
        )
        self.assertTrue(
            (self.root / "vendor/fixture/hello/run.sh").stat().st_mode & 0o111
        )
        self.assertEqual(self.run_cli("--validate").returncode, 0)

    def test_github_replacement_and_target_selection(self) -> None:
        self.assertEqual(self.from_github("--skill", "hello").returncode, 0)
        before = self.snapshot()
        result = self.from_github("--skill", "helper")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--replace", result.stderr)
        self.assertEqual(self.snapshot(), before)
        inventory = self.write_inventory(["hello", "helper"])
        result = self.from_github(
            "--replace",
            "--skill",
            "hello",
            "--skill",
            "helper",
            "--inventory",
            inventory,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.from_github("--replace", "--skill", "helper")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "vendor/fixture/hello").exists())

    def test_github_dependencies_and_invalid_selection_preserve_files(self) -> None:
        before = self.snapshot()
        for args, message in (
            (("--skill", "absent"), "No skill named absent"),
            (
                ("--skill", "hello", "--requires", "hello=absent"),
                "Missing skill dependency",
            ),
            (
                ("--skill", "hello", "--inventory", self.write_inventory(["absent"])),
                "Missing selected skill",
            ),
        ):
            with self.subTest(args=args):
                result = self.from_github(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(message, result.stderr)
                self.assertEqual(self.snapshot(), before)
        result = self.from_github(
            "--skill", "hello", "--skill", "helper", "--requires", "hello=helper"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        source = json.loads((self.root / "sources.json").read_text())["sources"][
            "fixture"
        ]
        self.assertEqual(source["skills"]["hello"]["requires"], ["helper"])

    def test_github_ambiguous_name_needs_path(self) -> None:
        duplicate = self.base / "upstream/other"
        duplicate.mkdir()
        (duplicate / "SKILL.md").write_bytes(
            (self.base / "upstream/skills/hello/SKILL.md").read_bytes()
        )
        self.repack()
        before = self.snapshot()
        result = self.from_github("--skill", "hello")
        self.assertEqual(result.returncode, 2)
        self.assertIn("hello=skills/hello", result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = self.from_github("--skill", "hello=skills/hello")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_github_license_discovery_and_override(self) -> None:
        upstream = self.base / "upstream"
        (upstream / "LICENSE").rename(upstream / "NOTICE")
        self.repack()
        before = self.snapshot()
        result = self.from_github("--skill", "hello")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--license-file", result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = self.from_github("--skill", "hello", "--license-file", "NOTICE")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_github_selected_links_fail_without_changes(self) -> None:
        (self.base / "upstream/skills/hello/unsafe").symlink_to("../../LICENSE")
        self.repack()
        before = self.snapshot()
        result = self.from_github("--skill", "hello")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unsupported imported archive entry", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_github_existing_integrity_is_checked(self) -> None:
        self.assertEqual(self.from_github("--skill", "hello").returncode, 0)
        (self.root / "vendor/fixture/hello/SKILL.md").write_text("Local edit")
        before = self.snapshot()
        result = self.from_github("--replace", "--skill", "hello")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Vendor content changed", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_shorthand_replacement_regenerates_only_selected_source_metadata(
        self,
    ) -> None:
        self.spec["skills"].pop("helper")
        self.spec["skills"]["hello"]["scope"] = "work"
        self.save_spec()
        self.assertEqual(self.onboard().returncode, 0)
        self.assertEqual(
            self.from_github("--skill", "helper", "--source-name", "other").returncode,
            0,
        )
        path = self.root / "sources.json"
        before = json.loads(path.read_text())
        before["sources"]["fixture"]["consumerNote"] = "Replaceable source metadata"
        before["sources"]["other"]["skills"]["helper"]["scope"] = "shared"
        local = self.root / "local/personal"
        local.mkdir(parents=True)
        (local / "SKILL.md").write_text(
            "---\nname: personal\ndescription: Personal fixture.\n---\n"
        )
        before["localSkills"] = {
            "personal": {"path": "local/personal", "scope": "personal"}
        }
        path.write_text(json.dumps(before))
        files = self.snapshot()
        result = self.from_github("--replace", "--skill", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(path.read_text())
        self.assertEqual(after["sources"]["other"], before["sources"]["other"])
        self.assertEqual(after["localSkills"], before["localSkills"])
        source = after["sources"]["fixture"]
        self.assertEqual(source["skills"], {"hello": {"path": "skills/hello"}})
        self.assertNotIn("license", source)
        self.assertNotIn("consumerNote", source)
        self.assertEqual(
            source["contentSha256"], before["sources"]["fixture"]["contentSha256"]
        )
        self.assertEqual(
            {k: v for k, v in self.snapshot().items() if k != "sources.json"},
            {k: v for k, v in files.items() if k != "sources.json"},
        )

    def test_ordinary_update_preserves_extra_metadata_and_dependencies(self) -> None:
        self.spec["skills"]["hello"].update(scope="work", requires=["helper"])
        self.save_spec()
        self.assertEqual(self.onboard().returncode, 0)
        path = self.root / "sources.json"
        before = json.loads(path.read_text())
        before["sources"]["fixture"]["consumerNote"] = "Retained by updates"
        path.write_text(json.dumps(before))
        result = skill_vendor.main(
            ["--root", str(self.root), "fixture"],
            resolve=lambda source: ("main", "b" * 40),
            fetch=lambda source, destination: shutil.copyfile(
                self.archive, destination
            ),
        )
        self.assertEqual(result, 0)
        source = json.loads(path.read_text())["sources"]["fixture"]
        expected = before["sources"]["fixture"] | {
            "revision": "b" * 40,
            "resolvedRef": "main",
        }
        self.assertEqual(source, expected)

    def test_github_uses_default_branch_and_downloads_resolved_commit(self) -> None:
        def api(path: str) -> skill_vendor.JSONValue:
            if path == "repos/example/fixture":
                return {"default_branch": "trunk"}
            self.assertEqual(path, "repos/example/fixture/commits/trunk")
            return {"sha": "b" * 40}

        def fetch(source: skill_vendor.Source, destination: Path) -> None:
            self.assertEqual(source["revision"], "b" * 40)
            shutil.copyfile(self.archive, destination)

        result = skill_vendor.main(
            [
                "--root",
                str(self.root),
                "--from",
                "https://github.com/example/fixture.git",
                "--skill",
                "hello",
            ],
            api=api,
            fetch=fetch,
        )
        self.assertEqual(result, 0)
        source = json.loads((self.root / "sources.json").read_text())["sources"][
            "fixture"
        ]
        self.assertEqual(source["revision"], "b" * 40)
        self.assertEqual(source["update"], {"kind": "branch", "ref": "trunk"})

    def test_github_concurrent_manifest_edit_is_preserved(self) -> None:
        concurrent = b'{"schemaVersion": 2, "sources": {}}\n'

        def fetch(source: skill_vendor.Source, destination: Path) -> None:
            shutil.copyfile(self.archive, destination)
            (self.root / "sources.json").write_bytes(concurrent)

        with self.assertRaises(SystemExit) as raised:
            skill_vendor.main(
                [
                    "--root",
                    str(self.root),
                    "--from",
                    "example/fixture",
                    "--ref",
                    "main",
                    "--revision",
                    "a" * 40,
                    "--skill",
                    "hello",
                ],
                fetch=fetch,
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual((self.root / "sources.json").read_bytes(), concurrent)
        self.assertFalse((self.root / "vendor").exists())

    def test_github_root_skill(self) -> None:
        upstream = self.base / "upstream"
        shutil.copyfile(upstream / "skills/hello/SKILL.md", upstream / "SKILL.md")
        shutil.rmtree(upstream / "skills")
        (upstream / "AGENTS.md").unlink()
        self.repack()
        result = self.from_github("--skill", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        source = json.loads((self.root / "sources.json").read_text())["sources"][
            "fixture"
        ]
        self.assertEqual(source["skills"]["hello"]["path"], ".")
        self.assertEqual(self.run_cli("--validate").returncode, 0)
        self.assertEqual(
            skill_vendor.main(
                ["--root", str(self.root), "--locked"],
                fetch=lambda source, destination: shutil.copyfile(
                    self.archive, destination
                ),
            ),
            0,
        )

    def test_github_local_license_needs_no_repository_license(self) -> None:
        upstream = self.base / "upstream"
        (upstream / "LICENSE").rename(upstream / "skills/hello/LICENSE")
        self.repack()
        result = self.from_github("--skill", "hello")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.root / "vendor/fixture/hello/LICENSE").read_bytes(),
            b"Synthetic licence\n",
        )

    def test_github_argument_errors_leave_files_unchanged(self) -> None:
        before = self.snapshot()
        for args in (
            ("--from", "https://example.com/repo", "--skill", "hello"),
            ("--from", "example/fixture"),
            ("--skill", "hello", "--validate"),
            (
                "--from",
                "example/fixture",
                "--skill",
                "hello",
                "--archive",
                str(self.archive),
            ),
            (
                "--from",
                "example/fixture",
                "--skill",
                "hello",
                "--ref",
                "main",
                "--revision",
                "short",
            ),
        ):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_github_collisions_and_same_pin_checksum_are_preserved(self) -> None:
        self.assertEqual(self.from_github("--skill", "hello").returncode, 0)
        before = self.snapshot()
        result = self.from_github("--skill", "hello", "--source-name", "second")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Duplicate or invalid skill", result.stderr)
        self.assertEqual(self.snapshot(), before)
        (self.base / "upstream/skills/hello/run.sh").write_text("Changed archive")
        self.repack()
        result = self.from_github("--replace", "--skill", "hello")
        self.assertEqual(result.returncode, 2)
        self.assertIn("checksum mismatch at the existing pin", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_github_failed_download_leaves_files_unchanged(self) -> None:
        def fetch(source: skill_vendor.Source, destination: Path) -> None:
            destination.write_bytes(b"incomplete download")
            raise OSError("Fixture download failure")

        before = self.snapshot()
        with self.assertRaises(SystemExit) as raised:
            skill_vendor.main(
                [
                    "--root",
                    str(self.root),
                    "--from",
                    "example/fixture",
                    "--skill",
                    "hello",
                    "--ref",
                    "main",
                    "--revision",
                    "a" * 40,
                ],
                fetch=fetch,
            )
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.snapshot(), before)

    def test_onboard_and_validate(self) -> None:
        result = self.onboard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Imported fixture", result.stdout)
        source = json.loads((self.root / "sources.json").read_text())["sources"][
            "fixture"
        ]
        self.assertEqual(source["revision"], "a" * 40)
        self.assertEqual(
            source["archiveSha256"],
            hashlib.sha256(self.archive.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            (self.root / "vendor/fixture/hello/LICENSE").read_text(),
            "Synthetic licence\n",
        )
        self.assertTrue(
            (self.root / "vendor/fixture/hello/run.sh").stat().st_mode & 0o111
        )
        result = self.run_cli("--validate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "local-note").read_text(), "Preserve this file.")

    def test_existing_source_needs_explicit_replace(self) -> None:
        self.assertEqual(self.onboard().returncode, 0)
        before = self.snapshot()
        result = self.onboard()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Source already exists", result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = self.onboard("--replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(), before)
        result = self.run_cli("--archive", self.archive, "--validate")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.snapshot(), before)

    def test_python_module_entrypoint(self) -> None:
        self.assertEqual(self.onboard().returncode, 0)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "skill_vendor",
                "--root",
                str(self.root),
                "--validate",
            ],
            cwd=self.base,
            env={**os.environ, "PYTHONPATH": str(self.library)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validated 2 skills", result.stdout)

    def test_failed_import_leaves_root_unchanged(self) -> None:
        before = self.snapshot()
        self.spec["skills"]["hello"]["path"] = "missing"
        self.save_spec()
        result = self.onboard()
        self.assertEqual(result.returncode, 2)
        self.assertIn("Missing upstream skill", result.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / "vendor").exists())

    def test_replace_removes_deselected_exports_at_same_pin(self) -> None:
        self.assertEqual(self.onboard().returncode, 0)
        hello = (self.root / "vendor/fixture/hello/SKILL.md").read_bytes()
        del self.spec["skills"]["helper"]
        self.save_spec()
        result = self.onboard("--replace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "vendor/fixture/helper").exists())
        self.assertEqual(
            (self.root / "vendor/fixture/hello/SKILL.md").read_bytes(), hello
        )
        self.assertEqual(self.run_cli("--validate").returncode, 0)

    def write_inventory(
        self, selected: list[str], skills: dict[str, Skill] | None = None
    ) -> Path:
        path = self.base / "inventory.json"
        path.write_text(
            json.dumps(
                {
                    "skills": skills or {},
                    "targets": {
                        "agent": {"path": ".agents/skills", "skills": selected}
                    },
                }
            )
        )
        return path

    def test_initial_import_selects_new_skills(self) -> None:
        self.spec["skills"]["hello"]["requires"] = ["helper"]
        self.save_spec()
        inventory = self.write_inventory(["hello", "helper"])
        result = self.onboard("--inventory", inventory)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("--validate", "--inventory", inventory)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_replacement_selects_new_exports(self) -> None:
        helper = self.spec["skills"].pop("helper")
        self.save_spec()
        self.assertEqual(self.onboard().returncode, 0)
        self.spec["skills"]["helper"] = helper
        self.spec["skills"]["hello"]["requires"] = ["helper"]
        self.save_spec()
        inventory = self.write_inventory(["hello", "helper"])
        result = self.onboard("--replace", "--inventory", inventory)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "vendor/fixture/helper/SKILL.md").is_file())
        result = self.run_cli("--validate", "--inventory", inventory)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_external_skill_can_depend_on_new_import(self) -> None:
        local = self.base / "local"
        local.mkdir()
        (local / "SKILL.md").write_text(
            "---\nname: local\ndescription: Local fixture.\n---\n"
        )
        inventory = self.write_inventory(
            ["local", "hello"],
            {
                "local": {"path": str(local), "requires": ["hello"]},
            },
        )
        result = self.onboard("--inventory", inventory)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("--validate", "--inventory", inventory)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_result_preserves_files_on_import_and_replacement(self) -> None:
        for replace in (False, True):
            self.spec["skills"]["hello"].pop("requires", None)
            self.save_spec()
            if replace:
                self.assertEqual(self.onboard().returncode, 0)
            for selected, required, message in (
                (["hello", "absent"], [], "Missing selected skill"),
                (["hello"], ["helper"], "Missing selected dependency"),
                (["hello", "helper"], ["absent"], "Missing skill dependency"),
            ):
                with self.subTest(
                    replace=replace, selected=selected, required=required
                ):
                    self.spec["skills"]["hello"]["requires"] = required
                    self.save_spec()
                    inventory = self.write_inventory(selected)
                    before = self.snapshot()
                    paths_before = sorted(
                        str(p.relative_to(self.root)) for p in self.root.rglob("*")
                    )
                    result = self.onboard(
                        *(["--replace"] if replace else []), "--inventory", inventory
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn(message, result.stderr)
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(
                        sorted(
                            str(p.relative_to(self.root)) for p in self.root.rglob("*")
                        ),
                        paths_before,
                    )

    def test_inventory_import_still_rejects_existing_vendor_edits(self) -> None:
        self.assertEqual(self.onboard().returncode, 0)
        (self.root / "vendor/fixture/hello/SKILL.md").write_text("Existing vendor edit")
        before = self.snapshot()
        inventory = self.write_inventory(["hello", "helper"])
        result = self.onboard("--replace", "--inventory", inventory)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Vendor content changed", result.stderr)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
