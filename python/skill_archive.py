"""Extract only declared upstream exports and their required licenses."""

import posixpath
import shutil
import tarfile
import tempfile
from pathlib import Path

from skill_inventory import (
    SkillError,
    Source,
    confined_path,
    digest_file,
    digest_tree,
    relative_path,
    require,
    validate_skill,
)


def selected_archive_members(
    source: Source, members: list[tarfile.TarInfo]
) -> tuple[str, list[tarfile.TarInfo]]:
    """Select trees and needed licenses without extracting unrelated members."""
    exports = [
        "." if skill["path"] == "." else str(relative_path(skill["path"]))
        for skill in source["skills"].values()
    ]
    # GitHub archives have one enclosing directory. Infer it from selected
    # exports, not from unrelated entries or the order of archive members.
    roots = set()
    for member in members:
        try:
            parts = relative_path(member.name.rstrip("/")).parts
        except SkillError:
            continue
        tail = "/".join(parts[1:])
        if any(
            (path == "." and tail == "SKILL.md")
            or tail == path
            or tail.startswith(path + "/")
            for path in exports
        ):
            roots.add(parts[0])
    require(bool(roots), f"Missing upstream skill: {exports[0]}")
    require(
        len(roots) == 1, "Ambiguous source archive directories for selected exports"
    )
    root = roots.pop()
    trees = [root if path == "." else root + "/" + path for path in exports]
    names = {member.name.rstrip("/") for member in members}
    licenses = []
    if any(tree + "/LICENSE" not in names for tree in trees):
        licenses = [root + "/" + str(relative_path(source["licenseFile"]))]

    def relevant(name: str) -> bool:
        return any(
            name == tree or name.startswith(tree + "/") or tree.startswith(name + "/")
            for tree in trees
        ) or any(name == file or file.startswith(name + "/") for file in licenses)

    selected = []
    seen = set()
    for member in members:
        name = member.name.rstrip("/")
        # Check both spelling and normalized location. Neither traversal into
        # an export nor traversal out of one is a supported imported path.
        normalized = posixpath.normpath(name.replace("\\", "/")).lstrip("/")
        if not relevant(name) and not relevant(normalized):
            continue
        relative_path(name)
        require(name not in seen, f"Duplicate imported archive path: {name}")
        seen.add(name)
        require(
            member.isdir() or member.isfile(),
            f"Unsupported imported archive entry (links and special files): {name}",
        )
        if name in trees or any(
            tree.startswith(name + "/") for tree in trees + licenses
        ):
            require(
                member.isdir(), f"Imported path ancestor is not a directory: {name}"
            )
        if name in licenses:
            require(member.isfile(), f"Invalid source licence: {name}")
        selected.append(member)
    return root, selected


def materialize(source: Source, archive: Path, destination: Path) -> str:
    require(
        digest_file(archive) == source["archiveSha256"],
        "Source archive checksum mismatch",
    )
    with tempfile.TemporaryDirectory(prefix="skill-source-") as temporary:
        extracted = Path(temporary)
        with tarfile.open(archive) as contents:
            root_name, members = selected_archive_members(source, contents.getmembers())
            contents.extractall(extracted, members=members, filter="data")
            # data_filter may clear group/other executable bits. Restore only
            # the ordinary executable bits, never special mode bits or ownership.
            for member in members:
                if member.isfile():
                    target = extracted / member.name
                    target.chmod(
                        (target.stat().st_mode & 0o666) | (member.mode & 0o111)
                    )
        upstream = extracted / root_name
        destination.mkdir()
        for name, skill in source["skills"].items():
            origin = (
                upstream
                if skill["path"] == "."
                else confined_path(upstream, skill["path"])
            )
            require(
                origin.is_dir() and not origin.is_symlink(),
                f"Missing upstream skill: {skill['path']}",
            )
            digest_tree(origin)  # Reject symlinks before copying selected content.
            shutil.copytree(origin, destination / name)
            license_file = destination / name / "LICENSE"
            if not license_file.exists():
                shutil.copyfile(
                    confined_path(upstream, source["licenseFile"]), license_file
                )
            require(license_file.is_file(), f"Invalid skill licence: {name}")
        for name in source["skills"]:
            validate_skill(destination / name, name)
        return digest_tree(destination)
