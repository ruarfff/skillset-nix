"""Exercise imports with local archives, without GitHub or installed agent state."""

import contextlib
import copy
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from functools import partial
from pathlib import Path
from typing import TypedDict, Unpack

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import skill_vendor as vendor
from skill_archive import selected_archive_members
from skill_inventory import (
    JSONValue,
    digest_tree,
    relative_path,
    validate_inventory,
    validate_skill,
)
from skill_publish import Publisher
from skill_scan import ScanOptions


class UpdateOptions(TypedDict, total=False):
    names: list[str]
    locked: bool
    check: bool
    extra: vendor.ExtraInventory
    publish: Publisher
    scan: ScanOptions


class FailureOptions(UpdateOptions, total=False):
    archive: Path


class ImportOptions(TypedDict, total=False):
    root: Path
    replace: bool
    extra: vendor.ExtraInventory
    publish: Publisher


class VendorTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "skills"
        for directory in ("local", "vendor"):
            (self.root / directory).mkdir(parents=True)
        self.archive = self.make_archive(
            "old", "old instructions", {"obsolete.md": "old"}
        )
        source = {
            "repository": "https://github.com/example/skills",
            "revision": "a" * 40,
            "resolvedRef": "main",
            "update": {"kind": "branch", "ref": "main"},
            "archiveSha256": vendor.digest_file(self.archive),
            "contentSha256": "0" * 64,
            "license": "MIT",
            "licenseFile": "LICENSE",
            "skills": {"example": {"path": "skills/example", "scope": "shared"}},
        }
        source["contentSha256"] = vendor.materialize(
            source, self.archive, self.root / "vendor/example"
        )
        self.manifest = {"schemaVersion": 2, "sources": {"example": source}}
        self.save_manifest()

    def save_manifest(self) -> None:
        (self.root / "sources.json").write_text(
            json.dumps(self.manifest, indent=2) + "\n"
        )

    def make_archive(
        self, name: str, body: str, extra: dict[str, str] | None = None
    ) -> Path:
        upstream = self.base / name
        skill = upstream / "skills/example"
        skill.mkdir(parents=True)
        (upstream / "LICENSE").write_text("Fixture licence\n")
        (skill / "SKILL.md").write_text(
            f"---\nname: example\ndescription: Fixture skill.\n---\n{body}\n"
        )
        for relative, content in (extra or {}).items():
            target = skill / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        archive = self.base / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            output.add(upstream, arcname="upstream")
        return archive

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }

    def update(
        self, archive: Path | None = None, **kwargs: Unpack[UpdateOptions]
    ) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return vendor.update(
                self.root,
                kwargs.pop("names", ["example"]),
                fetch=lambda source, target: shutil.copyfile(
                    archive or self.archive, target
                ),
                resolve=lambda source: ("main", "b" * 40),
                **kwargs,
            )

    def assert_update_fails_unchanged(
        self, error: type[Exception], message: str, **kwargs: Unpack[FailureOptions]
    ) -> None:
        before = self.snapshot()
        with self.assertRaisesRegex(error, message):
            self.update(**kwargs)
        self.assertEqual(self.snapshot(), before)

    def test_update_copies_support_files_and_removes_obsolete_files(self) -> None:
        self.update(
            self.make_archive(
                "new", "new instructions", {"references/new.md": "reference"}
            )
        )
        target = self.root / "vendor/example/example"
        self.assertIn("new instructions", (target / "SKILL.md").read_text())
        self.assertEqual((target / "references/new.md").read_text(), "reference")
        self.assertFalse((target / "obsolete.md").exists())
        self.assertEqual(
            vendor.read_manifest(self.root)["sources"]["example"]["revision"], "b" * 40
        )
        vendor.validate_tree(self.root)

    def test_check_reports_stale_without_writing_or_downloading(self) -> None:
        before = self.snapshot()
        self.assertEqual(self.update(self.base / "does-not-exist", check=True), 1)
        self.assertEqual(self.snapshot(), before)

    def test_locked_reproduces_without_writing(self) -> None:
        before = self.snapshot()
        self.update(locked=True)
        self.assertEqual(self.snapshot(), before)

    def test_locked_rejects_archive_with_wrong_checksum(self) -> None:
        self.assert_update_fails_unchanged(
            vendor.SkillError,
            "checksum mismatch",
            archive=self.make_archive("different", "changed"),
            locked=True,
        )

    def test_local_vendor_edits_are_preserved(self) -> None:
        (self.root / "vendor/example/example/SKILL.md").write_text("local edit")
        self.assert_update_fails_unchanged(vendor.SkillError, "Vendor content changed")

    def test_custom_content_is_preserved(self) -> None:
        skill = self.root / "local/private-skill"
        skill.mkdir()
        text = "---\nname: private-skill\ndescription: Local work skill.\n---\nCustom instructions.\n"
        (skill / "SKILL.md").write_text(text)
        self.manifest["localSkills"] = {
            "private-skill": {"path": "local/private-skill"}
        }
        self.save_manifest()
        self.update()
        self.assertEqual((skill / "SKILL.md").read_text(), text)

    def test_patch_records_are_rejected_without_dropping_local_policy(self) -> None:
        self.manifest["sources"]["example"]["patches"] = []
        self.save_manifest()
        self.assert_update_fails_unchanged(
            vendor.SkillError, "Vendor patches are not supported"
        )

    def test_legacy_schema_requires_explicit_migration(self) -> None:
        self.manifest["schemaVersion"] = 1
        self.save_manifest()
        self.assert_update_fails_unchanged(vendor.SkillError, "migrate local patches")

    def test_upstream_rewrite_does_not_conflict_with_custom_skill(self) -> None:
        custom = self.root / "local/local-example"
        shutil.copytree(self.root / "vendor/example/example", custom)
        instructions = custom / "SKILL.md"
        instructions.write_text(
            instructions.read_text().replace("name: example", "name: local-example")
            + "Local policy and workflow.\n"
        )
        before = digest_tree(custom)
        archive = self.make_archive(
            "rewritten",
            "Entirely rewritten upstream instructions.",
            {
                "scripts/new.sh": "#!/bin/sh\nexit 0\n",
                "LICENSE": "Skill-specific licence\n",
            },
        )
        # The import retains upstream executable bits as well as file bytes.
        (self.base / "rewritten/skills/example/scripts/new.sh").chmod(0o755)
        with tarfile.open(archive, "w:gz") as output:
            output.add(self.base / "rewritten", arcname="upstream")
        self.update(archive)
        self.assertEqual(digest_tree(custom), before)
        self.assertEqual(
            digest_tree(self.root / "vendor/example/example"),
            digest_tree(self.base / "rewritten/skills/example"),
        )
        vendor.validate_tree(self.root)

    def test_locked_rejects_content_that_does_not_match_upstream(self) -> None:
        # Even a locally rehashed snapshot must match the pinned archive.
        target = self.root / "vendor/example"
        (target / "example/SKILL.md").write_text(
            "---\nname: example\ndescription: Fixture skill.\n---\nLocal rewrite.\n"
        )
        self.manifest["sources"]["example"]["contentSha256"] = digest_tree(target)
        self.save_manifest()
        self.assert_update_fails_unchanged(
            vendor.SkillError, "does not reproduce", locked=True
        )

    def test_edits_during_fetch_are_preserved(self) -> None:
        target = self.root / "vendor/example/example/SKILL.md"

        def fetch(source: vendor.Source, destination: Path) -> None:
            shutil.copyfile(self.archive, destination)
            target.write_text("edit made while downloading")

        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(vendor.SkillError, "Vendor content changed"),
        ):
            vendor.update(
                self.root,
                ["example"],
                fetch=fetch,
                resolve=lambda source: ("main", "b" * 40),
            )
        self.assertEqual(target.read_text(), "edit made while downloading")
        self.assertEqual(
            vendor.read_manifest(self.root)["sources"]["example"]["revision"], "a" * 40
        )

    def test_missing_export_keeps_checkout_unchanged(self) -> None:
        self.manifest["sources"]["example"]["skills"]["example"]["path"] = (
            "skills/missing"
        )
        self.save_manifest()
        self.assert_update_fails_unchanged(vendor.SkillError, "Missing upstream skill")

    def test_invalid_upstream_license_keeps_checkout_unchanged(self) -> None:
        self.assert_update_fails_unchanged(
            vendor.SkillError,
            "Invalid skill licence",
            archive=self.make_archive(
                "invalid-license", "new instructions", {"LICENSE/nested": "invalid"}
            ),
        )

    def test_batch_failure_does_not_publish_first_source(self) -> None:
        self.manifest["sources"]["second"] = copy.deepcopy(
            self.manifest["sources"]["example"]
        )
        source = self.manifest["sources"]["second"]
        source["skills"] = {"second-skill": {"path": "skills/missing", "scope": "work"}}
        dest = self.root / "vendor/second/second-skill"
        shutil.copytree(self.root / "vendor/example/example", dest)
        p = dest / "SKILL.md"
        p.write_text(p.read_text().replace("name: example", "name: second-skill"))
        source["contentSha256"] = digest_tree(dest.parent)
        self.save_manifest()
        self.assert_update_fails_unchanged(
            vendor.SkillError, "Missing upstream skill", names=["example", "second"]
        )

    def test_publish_failure_rolls_back_snapshots_and_manifest(self) -> None:
        original_rename = Path.rename

        def rename(path: Path, target: Path) -> Path:
            if path.name == "sources.json" and path.parent.name == "prepared":
                raise OSError("simulated write failure")
            return original_rename(path, target)

        self.assert_update_fails_unchanged(
            OSError,
            "simulated write failure",
            publish=partial(vendor.publish, rename=rename),
        )

    def test_missing_download_does_not_change_checkout(self) -> None:
        self.assert_update_fails_unchanged(
            OSError, "No such file", archive=self.base / "missing.tar.gz"
        )

    def test_legacy_scope_is_inert_and_duplicate_names_are_rejected(self) -> None:
        self.manifest["sources"]["example"]["skills"]["example"]["scope"] = "personal"
        self.save_manifest()
        vendor.validate_tree(self.root)
        shutil.copytree(
            self.root / "vendor/example/example", self.root / "local/example"
        )
        self.manifest["localSkills"] = {"example": {"path": "local/example"}}
        self.save_manifest()
        with self.assertRaisesRegex(vendor.SkillError, "Duplicate skill"):
            vendor.validate_tree(self.root)

    def test_dependencies_use_actual_inventory_and_target_selection(self) -> None:
        directory = self.root / "local/helper"
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            "---\nname: helper\ndescription: Help.\n---\n"
        )
        self.manifest["localSkills"] = {"helper": {"path": "local/helper"}}
        self.manifest["sources"]["example"]["skills"]["example"]["requires"] = [
            "helper"
        ]
        self.save_manifest()
        vendor.validate_tree(self.root)
        extra = {
            "targets": {"agent": {"path": ".agents/skills", "skills": ["example"]}}
        }
        with self.assertRaisesRegex(vendor.SkillError, "Missing selected dependency"):
            vendor.validate_tree(self.root, extra=extra)
        extra["targets"]["agent"]["skills"].append("helper")
        vendor.validate_tree(self.root, extra=extra)

    def test_selected_symlinks_are_rejected(self) -> None:
        directory = self.base / "linked"
        directory.mkdir()
        (directory / "outside").symlink_to(self.base / "old/LICENSE")
        with self.assertRaisesRegex(vendor.SkillError, "Symlinks are not supported"):
            digest_tree(directory)

    def test_empty_inventory_and_disabled_targets(self) -> None:
        empty = self.base / "empty"
        empty.mkdir()
        (empty / "sources.json").write_text('{"schemaVersion":2,"sources":{}}')
        self.assertEqual(vendor.validate_tree(empty), {})
        self.assertEqual(vendor.update(empty, [], check=True), 0)
        self.assertEqual(
            validate_inventory(
                {}, {"off": {"enable": False, "path": "../bad", "skills": ["missing"]}}
            ),
            {},
        )

    def test_invalid_relative_paths(self) -> None:
        for path in (
            "",
            ".",
            "..",
            "../outside",
            "/absolute",
            "a/../b",
            "a//b",
            "a/./b",
            "a\\b",
            "a\nb",
        ):
            with self.subTest(path=path), self.assertRaises(vendor.SkillError):
                relative_path(path)

    def test_missing_resource_is_rejected_before_publication(self) -> None:
        self.assert_update_fails_unchanged(
            vendor.SkillError,
            "Missing skill resource",
            archive=self.make_archive(
                "missing-resource", "[Read](./references/missing.md)"
            ),
        )

    def test_local_skill_symlink_is_rejected(self) -> None:
        (self.root / "local/linked").symlink_to(
            self.root / "vendor/example/example", target_is_directory=True
        )
        self.manifest["localSkills"] = {"example": {"path": "local/linked"}}
        self.save_manifest()
        with self.assertRaisesRegex(vendor.SkillError, "Symlinks are not supported"):
            vendor.validate_tree(self.root)

    def test_linked_vendor_parent_is_rejected(self) -> None:
        (self.root / "vendor").rename(self.root / "actual-vendor")
        (self.root / "vendor").symlink_to(
            self.root / "actual-vendor", target_is_directory=True
        )
        with self.assertRaisesRegex(vendor.SkillError, "Symlinks are not supported"):
            vendor.validate_tree(self.root)

    def append_archive_entries(self, entries: list[tuple[str, bytes]]) -> Path:
        archive = self.base / "modified.tar.gz"
        with (
            tarfile.open(self.archive) as original,
            tarfile.open(archive, "w:gz") as output,
        ):
            for member in original.getmembers():
                output.addfile(
                    member, original.extractfile(member) if member.isfile() else None
                )
            for name, kind in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "CLAUDE.md"
                output.addfile(member)
        return archive

    def test_out_of_scope_archive_entries_are_never_extracted(self) -> None:
        archive = self.append_archive_entries(
            [
                ("upstream/AGENTS.md", tarfile.SYMTYPE),
                ("upstream/unselected/hardlink", tarfile.LNKTYPE),
                ("upstream/unselected/pipe", tarfile.FIFOTYPE),
                ("upstream/unselected/../../outside", tarfile.REGTYPE),
                ("/unrelated/absolute", tarfile.REGTYPE),
            ]
        )
        with tarfile.open(archive) as contents:
            _, selected = selected_archive_members(
                self.manifest["sources"]["example"], contents.getmembers()
            )
        extracted = [member.name for member in selected]
        self.update(archive)
        self.assertEqual(
            digest_tree(self.root / "vendor/example"),
            self.manifest["sources"]["example"]["contentSha256"],
        )
        self.assertEqual(
            set(extracted),
            {
                "upstream",
                "upstream/LICENSE",
                "upstream/skills",
                "upstream/skills/example",
                "upstream/skills/example/SKILL.md",
                "upstream/skills/example/obsolete.md",
            },
        )

    def test_archive_links_special_files_and_traversal_are_rejected(self) -> None:
        for name, kind in [
            ("upstream/skills/example/link", tarfile.SYMTYPE),
            ("upstream/skills/example/link", tarfile.LNKTYPE),
            ("upstream/skills/example/pipe", tarfile.FIFOTYPE),
            ("upstream/skills/example/device", tarfile.CHRTYPE),
            ("upstream/skills/example/../../escape", tarfile.REGTYPE),
            ("upstream/unrelated/../skills/example/escape", tarfile.REGTYPE),
            ("/upstream/skills/example/escape", tarfile.REGTYPE),
            ("upstream/skills/example\\escape", tarfile.REGTYPE),
        ]:
            with self.subTest(name=name, kind=kind):
                self.assert_update_fails_unchanged(
                    vendor.SkillError,
                    "Unsafe relative path|Unsupported imported archive entry",
                    archive=self.append_archive_entries([(name, kind)]),
                )

    def test_imported_ancestors_and_license_links_are_rejected(self) -> None:
        for name in (
            "upstream",
            "upstream/skills",
            "upstream/skills/example",
            "upstream/LICENSE",
        ):
            for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.REGTYPE):
                with self.subTest(name=name, kind=kind):
                    self.assert_update_fails_unchanged(
                        vendor.SkillError,
                        "Duplicate imported archive path",
                        archive=self.append_archive_entries([(name, kind)]),
                    )

    def test_links_and_files_replacing_imported_ancestors_are_rejected(self) -> None:
        for name in (
            "upstream",
            "upstream/skills",
            "upstream/skills/example",
            "upstream/LICENSE",
        ):
            kinds = (
                (tarfile.SYMTYPE, tarfile.LNKTYPE)
                if name.endswith("LICENSE")
                else (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.REGTYPE)
            )
            for kind in kinds:
                archive = self.base / "replaced-entry.tar.gz"
                with (
                    tarfile.open(self.archive) as original,
                    tarfile.open(archive, "w:gz") as output,
                ):
                    for member in original.getmembers():
                        if member.name == name:
                            replacement = tarfile.TarInfo(name)
                            replacement.type = kind
                            replacement.linkname = "elsewhere"
                            output.addfile(replacement)
                        else:
                            output.addfile(
                                member,
                                original.extractfile(member)
                                if member.isfile()
                                else None,
                            )
                with self.subTest(name=name, kind=kind):
                    self.assert_update_fails_unchanged(
                        vendor.SkillError,
                        "Unsupported imported archive entry|ancestor is not a directory",
                        archive=archive,
                    )

    def test_unused_source_license_link_is_ignored(self) -> None:
        archive = self.make_archive(
            "own-license", "instructions", {"LICENSE": "Own licence"}
        )
        with (
            tarfile.open(archive) as original,
            tarfile.open(self.base / "license-link.tar.gz", "w:gz") as output,
        ):
            for member in original.getmembers():
                if member.name == "upstream/LICENSE":
                    member.type = tarfile.SYMTYPE
                    member.linkname = "elsewhere"
                    member.size = 0
                output.addfile(
                    member, original.extractfile(member) if member.isfile() else None
                )
        self.update(self.base / "license-link.tar.gz")
        self.assertEqual(
            (self.root / "vendor/example/example/LICENSE").read_text(), "Own licence"
        )

    def test_executable_bit_edits_are_detected(self) -> None:
        (self.root / "vendor/example/example/SKILL.md").chmod(0o755)
        self.assert_update_fails_unchanged(vendor.SkillError, "Vendor content changed")

    def test_preserves_all_executable_bits(self) -> None:
        archive = self.make_archive(
            "modes", "Commands", {"scripts/run": "#!/bin/sh\nexit 0\n"}
        )
        script = self.base / "modes/skills/example/scripts/run"
        script.chmod(0o651)
        with tarfile.open(archive, "w:gz") as output:
            output.add(self.base / "modes", arcname="upstream")
        self.update(archive)
        imported = self.root / "vendor/example/example/scripts/run"
        self.assertEqual(imported.stat().st_mode & 0o111, 0o011)
        self.assertEqual(imported.read_bytes(), script.read_bytes())

    def test_missing_inventory_dependency(self) -> None:
        self.manifest["sources"]["example"]["skills"]["example"]["requires"] = [
            "missing"
        ]
        self.save_manifest()
        self.assert_update_fails_unchanged(
            vendor.SkillError, "Missing skill dependency"
        )

    def test_target_selection_and_collision_errors(self) -> None:
        inventory = vendor.validate_tree(self.root)
        for target, message in [
            (
                {"path": ".agents/skills", "skills": ["missing"]},
                "Missing selected skill",
            ),
            (
                {"path": ".agents/skills", "skills": ["example", "example"]},
                "Duplicate installed names",
            ),
            ({"path": "../skills", "skills": ["example"]}, "Unsafe relative path"),
        ]:
            with (
                self.subTest(target=target),
                self.assertRaisesRegex(vendor.SkillError, message),
            ):
                validate_inventory(inventory, {"agent": target})
        target = {"path": ".agents/skills", "skills": ["example"]}
        with self.assertRaisesRegex(vendor.SkillError, "Overlapping installed paths"):
            validate_inventory(inventory, {"a": target, "b": target})
        validate_inventory(
            inventory, {"a": target, "b": dict(target, path="other/skills")}
        )

    def test_explicit_inventory_supports_external_skill_dependencies(self) -> None:
        helper = self.base / "helper"
        helper.mkdir()
        (helper / "SKILL.md").write_text("---\nname: helper\ndescription: Help.\n---\n")
        self.manifest["sources"]["example"]["skills"]["example"]["requires"] = [
            "helper"
        ]
        self.save_manifest()
        extra = {
            "skills": {"helper": {"path": str(helper)}},
            "targets": {
                "agent": {"path": ".agents/skills", "skills": ["example", "helper"]}
            },
        }
        self.update(extra=extra)
        vendor.validate_tree(self.root, extra=extra)

    def test_edits_to_manifest_during_check_and_locked_are_detected(self) -> None:
        def edit() -> None:
            (self.root / "sources.json").write_bytes(
                (self.root / "sources.json").read_bytes() + b"\n"
            )

        def resolve(source: vendor.Source) -> tuple[str, str]:
            edit()
            return "main", source["revision"]

        with self.assertRaisesRegex(vendor.SkillError, "sources.json changed"):
            vendor.update(self.root, ["example"], check=True, resolve=resolve)
        self.save_manifest()

        def fetch(source: vendor.Source, target: Path) -> None:
            edit()
            shutil.copyfile(self.archive, target)

        with self.assertRaisesRegex(vendor.SkillError, "sources.json changed"):
            vendor.update(self.root, ["example"], locked=True, fetch=fetch)

    def test_edits_during_publication_preparation_are_preserved(self) -> None:
        original_copytree = shutil.copytree
        target = self.root / "vendor/example/example/SKILL.md"

        def copytree(source: Path, destination: Path) -> Path:
            result = original_copytree(source, destination)
            if Path(destination).parent.name == "prepared":
                target.write_text("concurrent publication edit")
            return result

        with self.assertRaisesRegex(vendor.SkillError, "Vendor content changed"):
            self.update(publish=partial(vendor.publish, copy_tree=copytree))
        self.assertEqual(target.read_text(), "concurrent publication edit")
        self.assertEqual(
            vendor.read_manifest(self.root)["sources"]["example"]["revision"], "a" * 40
        )

    def test_changed_candidate_copy_cannot_be_published(self) -> None:
        before = self.snapshot()

        def copytree(source: Path, destination: Path) -> Path:
            result = shutil.copytree(source, destination)
            (destination / "example/SKILL.md").write_text("Changed candidate")
            return result

        with self.assertRaisesRegex(vendor.SkillError, "Vendor content changed"):
            self.update(publish=partial(vendor.publish, copy_tree=copytree))
        self.assertEqual(self.snapshot(), before)

    def test_reviewed_personal_content_is_checked_before_publication(self) -> None:
        personal = self.root / "local/personal"
        personal.mkdir()
        note = personal / "SKILL.md"
        note.write_text("---\nname: personal\ndescription: Personal fixture.\n---\n")
        self.manifest["localSkills"] = {"personal": {"path": "local/personal"}}
        self.save_manifest()
        reviewed = {
            name: digest_tree(Path(skill["path"]))
            for name, skill in vendor.validate_tree(self.root).items()
        }

        def copytree(source: Path, destination: Path) -> Path:
            result = shutil.copytree(source, destination)
            note.write_text(note.read_text() + "Concurrent personal edit\n")
            return result

        with self.assertRaisesRegex(vendor.SkillError, "Scanned skill content changed"):
            vendor.publish(
                self.root,
                self.manifest,
                {"example": self.root / "vendor/example"},
                (self.root / "sources.json").read_bytes(),
                reviewed=reviewed,
                copy_tree=copytree,
            )
        self.assertIn("Concurrent personal edit", note.read_text())
        self.assertEqual(
            vendor.read_manifest(self.root)["sources"]["example"]["revision"], "a" * 40
        )

    def test_second_updater_fails_without_writing(self) -> None:
        def fetch(source: vendor.Source, target: Path) -> None:
            with self.assertRaisesRegex(vendor.SkillError, "Another skillset update"):
                vendor.update(self.root, ["example"], check=True)
            shutil.copyfile(self.archive, target)

        vendor.update(self.root, ["example"], locked=True, fetch=fetch)

    def test_check_current_and_cli_exit_codes(self) -> None:
        self.assertEqual(
            vendor.update(
                self.root,
                ["example"],
                check=True,
                resolve=lambda source: ("main", "a" * 40),
            ),
            0,
        )
        for result, revision in ((0, "a" * 40), (1, "b" * 40)):
            with self.subTest(result=result):
                self.assertEqual(
                    vendor.main(
                        ["--root", str(self.root), "--check"],
                        resolve=lambda source, revision=revision: ("main", revision),
                    ),
                    result,
                )
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                vendor.main(["--root", str(self.root), "--check", "absent"])
            self.assertEqual(raised.exception.code, 2)

    def test_release_policy_selects_highest_stable_matching_tag(self) -> None:
        source = copy.deepcopy(self.manifest["sources"]["example"])
        source["update"] = {"kind": "release", "tagPrefix": "skill-v"}
        releases = [
            {"tag_name": tag, "draft": draft, "prerelease": pre}
            for tag, draft, pre in [
                ("skill-v1.9.0", False, False),
                ("skill-v1.10.0", False, False),
                ("skill-v2.0.0", True, False),
                ("skill-v3.0.0", False, True),
                ("v9.0.0", False, False),
                ("skill-v4.0.0-beta", False, False),
            ]
        ]
        requests: list[str] = []

        def api(path: str) -> JSONValue:
            requests.append(path)
            return releases if "/releases?" in path else {"sha": "c" * 40}

        self.assertEqual(
            vendor.resolve_revision(source, api=api), ("skill-v1.10.0", "c" * 40)
        )
        self.assertTrue(requests[-1].endswith("/commits/skill-v1.10.0"))

    def test_branch_ref_is_url_encoded(self) -> None:
        source = copy.deepcopy(self.manifest["sources"]["example"])
        source["update"] = {"kind": "branch", "ref": "release/stable"}
        requests: list[str] = []

        def api(path: str) -> JSONValue:
            requests.append(path)
            return {"sha": "c" * 40}

        vendor.resolve_revision(source, api=api)
        self.assertTrue(requests[-1].endswith("/commits/release%2Fstable"))

    def test_source_names_do_not_collide_with_publication_backups(self) -> None:
        source = self.manifest["sources"].pop("example")
        self.manifest["sources"]["backup-0"] = source
        (self.root / "vendor/example").rename(self.root / "vendor/backup-0")
        self.save_manifest()
        self.update(names=["backup-0"])
        vendor.validate_tree(self.root)

    def test_invalid_upstream_commit_responses_are_rejected(self) -> None:
        source = self.manifest["sources"]["example"]
        responses: list[JSONValue] = [None, [], {"sha": 123}, {"sha": "not-a-pin"}]
        for response in responses:
            with (
                self.subTest(response=response),
                self.assertRaisesRegex(vendor.SkillError, "Invalid upstream commit"),
            ):
                vendor.resolve_revision(
                    source, api=lambda path, response=response: response
                )

    def test_invalid_or_empty_release_responses_are_rejected(self) -> None:
        source = copy.deepcopy(self.manifest["sources"]["example"])
        source["update"] = {"kind": "release", "tagPrefix": "v"}
        responses: list[JSONValue] = [
            None,
            [None],
            [],
            [{"draft": False, "prerelease": False, "tag_name": 123}],
            [{"draft": False, "prerelease": False, "tag_name": "v1.0.0-beta"}],
        ]
        for response in responses:
            with self.subTest(response=response), self.assertRaises(vendor.SkillError):
                vendor.resolve_revision(
                    source, api=lambda path, response=response: response
                )

    def test_rollback_failure_retains_recovery_files(self) -> None:
        original_rename = Path.rename

        def rename(path: Path, target: Path) -> Path:
            if path.name == "sources.json" and path.parent.name == "prepared":
                raise OSError("publication failed")
            if path.parent.name == "backups":
                raise OSError("rollback failed")
            return original_rename(path, target)

        with self.assertRaisesRegex(vendor.SkillError, "backups retained"):
            self.update(publish=partial(vendor.publish, rename=rename))
        backups = list(self.base.glob(".skills-publish-*/backups"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(
            (backups[0] / "1").read_text(), json.dumps(self.manifest, indent=2) + "\n"
        )
        self.assertTrue((backups[0] / "0/example/SKILL.md").is_file())

    def test_invalid_frontmatter_and_missing_skill_file(self) -> None:
        skill = self.base / "invalid-skill"
        skill.mkdir()
        with self.assertRaisesRegex(vendor.SkillError, "Missing SKILL.md"):
            validate_skill(skill, "example")
        for text in (
            "No frontmatter",
            "---\nname: other\ndescription: Other.\n---\n",
            "---\nname: example\n---\n",
            "---\nname: example\ndescription: ''\n---\n",
            '---\nname: example\ndescription: ""\n---\n',
            "---\nname: example\ndescription: |\n---\n",
        ):
            (skill / "SKILL.md").write_text(text)
            with self.subTest(text=text), self.assertRaises(vendor.SkillError):
                validate_skill(skill, "example")

    def import_fixture(self) -> tuple[vendor.ImportSpec, Path]:
        archive = self.make_archive(
            "import", "Second skill", {"scripts/run.sh": "#!/bin/sh\ntrue\n"}
        )
        tree = self.base / "import/skills/example"
        tree.rename(self.base / "import/skills/second")
        tree = self.base / "import/skills/second"
        (tree / "SKILL.md").write_text(
            (tree / "SKILL.md").read_text().replace("name: example", "name: second")
        )
        (tree / "scripts/run.sh").chmod(0o755)
        with tarfile.open(archive, "w:gz") as output:
            output.add(self.base / "import", arcname="upstream")
        spec = {
            "name": "second",
            "repository": "https://github.com/example/skills",
            "update": {"kind": "branch", "ref": "main"},
            "license": "MIT",
            "licenseFile": "LICENSE",
            "skills": {"second": {"path": "skills/second"}},
        }
        return spec, archive

    def import_source(
        self, spec: vendor.ImportSpec, archive: Path, **kwargs: Unpack[ImportOptions]
    ) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return vendor.import_source(
                kwargs.pop("root", self.root),
                spec,
                fetch=lambda source, target: shutil.copyfile(archive, target),
                resolve=lambda source: ("main", "b" * 40),
                **kwargs,
            )

    def test_import_initializes_manifest_only_after_preparation(self) -> None:
        spec, archive = self.import_fixture()
        root = self.base / "fresh"
        root.mkdir()
        (root / "notes.txt").write_text("Keep local notes")
        self.import_source(spec, archive, root=root)
        manifest = vendor.read_manifest(root)
        source = manifest["sources"]["second"]
        self.assertEqual(source["revision"], "b" * 40)
        self.assertEqual(source["resolvedRef"], "main")
        self.assertEqual(source["archiveSha256"], vendor.digest_file(archive))
        self.assertEqual(source["contentSha256"], digest_tree(root / "vendor/second"))
        self.assertEqual((root / "notes.txt").read_text(), "Keep local notes")
        self.assertEqual(
            (root / "vendor/second/second/scripts/run.sh").stat().st_mode & 0o111, 0o111
        )
        vendor.validate_tree(root)

    def test_import_preserves_existing_sources_and_local_skills(self) -> None:
        spec, archive = self.import_fixture()
        local = self.root / "local/helper"
        local.mkdir()
        (local / "SKILL.md").write_text("---\nname: helper\ndescription: Help.\n---\n")
        self.manifest["localSkills"] = {"helper": {"path": "local/helper"}}
        self.save_manifest()
        before = self.snapshot()
        self.import_source(spec, archive)
        after = self.snapshot()
        for path, content in before.items():
            if path != "sources.json":
                self.assertEqual(after[path], content)
        manifest = vendor.read_manifest(self.root)
        self.assertEqual(manifest["localSkills"], self.manifest["localSkills"])
        self.assertEqual(
            manifest["sources"]["example"], self.manifest["sources"]["example"]
        )

    def test_import_requires_explicit_replacement_and_existing_source(self) -> None:
        spec, archive = self.import_fixture()
        before = self.snapshot()
        with self.assertRaisesRegex(vendor.SkillError, "Cannot replace missing source"):
            self.import_source(spec, archive, replace=True)
        spec["name"] = "example"
        with self.assertRaisesRegex(vendor.SkillError, "Source already exists"):
            self.import_source(spec, archive)
        self.assertEqual(self.snapshot(), before)

    def test_import_skill_collision_fails_before_fetch(self) -> None:
        spec, _archive = self.import_fixture()
        spec["skills"] = {"example": {"path": "skills/example"}}
        before = self.snapshot()
        with self.assertRaisesRegex(
            vendor.SkillError, "Duplicate or invalid skill|collides"
        ):
            self.import_source(spec, self.base / "does-not-exist")
        self.assertEqual(self.snapshot(), before)
        spec["skills"] = {"second": {"path": "skills/second"}}
        with self.assertRaisesRegex(vendor.SkillError, "collides"):
            self.import_source(
                spec,
                self.base / "does-not-exist",
                extra={
                    "skills": {
                        "second": {"path": str(self.base / "import/skills/second")}
                    }
                },
            )
        self.assertEqual(self.snapshot(), before)

    def test_import_replacement_removes_old_exports_and_preserves_local_content(
        self,
    ) -> None:
        spec, archive = self.import_fixture()
        spec["name"] = "example"
        (self.root / "local/note").write_text("Keep this")
        self.import_source(spec, archive, replace=True)
        self.assertFalse((self.root / "vendor/example/example").exists())
        self.assertTrue((self.root / "vendor/example/second/SKILL.md").is_file())
        self.assertEqual((self.root / "local/note").read_text(), "Keep this")
        self.assertEqual(
            set(vendor.read_manifest(self.root)["sources"]["example"]["skills"]),
            {"second"},
        )
        vendor.validate_tree(self.root)

    def test_import_replacement_checks_selected_dependencies_before_publication(
        self,
    ) -> None:
        spec, archive = self.import_fixture()
        spec["name"] = "example"
        before = self.snapshot()
        with self.assertRaisesRegex(vendor.SkillError, "Missing selected skill"):
            self.import_source(
                spec,
                archive,
                replace=True,
                extra={
                    "targets": {
                        "agent": {"path": ".agents/skills", "skills": ["example"]}
                    }
                },
            )
        self.assertEqual(self.snapshot(), before)

    def test_import_failure_leaves_empty_root_empty(self) -> None:
        spec, archive = self.import_fixture()
        root = self.base / "fresh"
        root.mkdir()
        spec["skills"]["second"]["path"] = "missing"
        with self.assertRaisesRegex(vendor.SkillError, "Missing upstream skill"):
            self.import_source(spec, archive, root=root)
        self.assertEqual(list(root.iterdir()), [])

    def test_import_fetch_and_dependency_failures_leave_checkout_unchanged(
        self,
    ) -> None:
        spec, archive = self.import_fixture()
        before = self.snapshot()
        with self.assertRaises(OSError):
            self.import_source(spec, self.base / "missing")
        self.assertEqual(self.snapshot(), before)
        spec["skills"]["second"]["requires"] = ["missing"]
        with self.assertRaisesRegex(vendor.SkillError, "Missing skill dependency"):
            self.import_source(spec, archive)
        self.assertEqual(self.snapshot(), before)

    def test_import_publication_failure_rolls_back_new_and_replaced_sources(
        self,
    ) -> None:
        spec, archive = self.import_fixture()
        original_rename = Path.rename

        def rename(path: Path, target: Path) -> Path:
            if path.name == "sources.json" and path.parent.name == "prepared":
                raise OSError("simulated publication failure")
            return original_rename(path, target)

        for replace in (False, True):
            spec["name"] = "example" if replace else "second"
            before = self.snapshot()
            with self.assertRaisesRegex(OSError, "publication failure"):
                self.import_source(
                    spec,
                    archive,
                    replace=replace,
                    publish=partial(vendor.publish, rename=rename),
                )
            self.assertEqual(self.snapshot(), before)
            self.assertFalse((self.root / "vendor/second").exists())
        root = self.base / "fresh"
        root.mkdir()
        with self.assertRaisesRegex(OSError, "publication failure"):
            self.import_source(
                spec, archive, root=root, publish=partial(vendor.publish, rename=rename)
            )
        self.assertEqual(list(root.iterdir()), [])

    def test_import_detects_edits_during_fetch_and_uses_existing_lock(self) -> None:
        spec, archive = self.import_fixture()
        target = self.root / "vendor/example/example/SKILL.md"

        def fetch(source: vendor.Source, destination: Path) -> None:
            with self.assertRaisesRegex(vendor.SkillError, "Another skillset update"):
                vendor.import_source(self.root, spec)
            shutil.copyfile(archive, destination)
            target.write_text("Concurrent vendor edit")

        with self.assertRaisesRegex(vendor.SkillError, "Vendor content changed"):
            vendor.import_source(
                self.root, spec, fetch=fetch, resolve=lambda source: ("main", "b" * 40)
            )
        self.assertEqual(target.read_text(), "Concurrent vendor edit")
        self.assertEqual(vendor.read_manifest(self.root), self.manifest)
        self.assertFalse((self.root / "vendor/second").exists())

    def test_import_local_archive_requires_pin_and_skips_network(self) -> None:
        spec, archive = self.import_fixture()
        with self.assertRaisesRegex(
            vendor.SkillError, "explicit 40-character revision"
        ):
            vendor.import_source(self.root, spec, archive=archive)
        spec["revision"] = "c" * 40
        vendor.import_source(
            self.root,
            spec,
            archive=archive,
            fetch=lambda *_: self.fail("unexpected fetch"),
            resolve=lambda *_: self.fail("unexpected resolve"),
        )
        self.assertEqual(
            vendor.read_manifest(self.root)["sources"]["second"]["revision"], "c" * 40
        )

    def test_import_replacement_keeps_archive_checksum_at_same_pin(self) -> None:
        spec, archive = self.import_fixture()
        spec["name"] = "example"
        spec["revision"] = "a" * 40
        before = self.snapshot()
        with self.assertRaisesRegex(
            vendor.SkillError, "checksum mismatch at the existing pin"
        ):
            self.import_source(spec, archive, replace=True)
        self.assertEqual(self.snapshot(), before)
        spec["skills"] = {"example": {"path": "skills/example"}}
        self.import_source(spec, self.archive, replace=True)
        self.assertEqual(
            vendor.read_manifest(self.root)["sources"]["example"]["contentSha256"],
            self.manifest["sources"]["example"]["contentSha256"],
        )

    def test_import_rejects_lock_fields_and_invalid_pins(self) -> None:
        spec, archive = self.import_fixture()
        before = self.snapshot()
        for field, value in (
            ("patches", []),
            ("contentSha256", "0" * 64),
            ("revision", "main"),
        ):
            with self.subTest(field=field), self.assertRaises(vendor.SkillError):
                self.import_source(dict(spec, **{field: value}), archive)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
