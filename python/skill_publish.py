"""Publish prepared snapshots under a directory lock, with rollback on failure."""

import fcntl
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from skill_inventory import (
    ExtraInventory,
    Manifest,
    SkillError,
    confined_path,
    digest_tree,
    require,
    validate_tree,
)

type CopyTree = Callable[[Path, Path], Path]
type Rename = Callable[[Path, Path], Path]
type Publisher = Callable[
    [
        Path,
        Manifest,
        dict[str, Path],
        bytes | None,
        ExtraInventory | None,
        dict[str, str] | None,
    ],
    None,
]


def manifest_bytes(root: Path) -> bytes | None:
    path = confined_path(root, "sources.json")
    return path.read_bytes() if path.exists() else None


def publish(
    root: Path,
    manifest: Manifest,
    staged: dict[str, Path],
    original_manifest: bytes | None,
    extra: ExtraInventory | None = None,
    reviewed: dict[str, str] | None = None,
    *,
    copy_tree: CopyTree = shutil.copytree,
    rename: Rename = Path.rename,
) -> None:
    """Stage on the same filesystem and roll back if publishing any path fails."""
    require(
        manifest_bytes(root) == original_manifest,
        "sources.json changed during the update; retry after reviewing it",
    )
    work = Path(tempfile.mkdtemp(prefix=".skills-publish-", dir=root.parent))
    cleanup = True
    try:
        prepared, backups = _prepare(
            root, manifest, staged, original_manifest, extra, work, copy_tree, reviewed
        )
        replacements = [(prepared / name, root / "vendor" / name) for name in staged]
        replacements.append((prepared / "sources.json", root / "sources.json"))
        applied = []
        vendor_root = root / "vendor"
        created_vendor = False
        try:
            if not vendor_root.exists():
                vendor_root.mkdir()
                created_vendor = True
            for index, (new, target) in enumerate(replacements):
                backup = backups / str(index) if target.exists() else None
                if backup is not None:
                    rename(target, backup)
                applied.append((target, backup))
                rename(new, target)
        except BaseException:
            try:
                _rollback(applied, vendor_root, created_vendor, rename)
            except BaseException as error:
                cleanup = False
                raise SkillError(
                    f"Publication rollback failed; backups retained at {work}: {error}"
                ) from error
            raise
    finally:
        if cleanup:
            shutil.rmtree(work)


@contextmanager
def locked_root(root: Path) -> Iterator[None]:
    # Advisory lock on the directory: no lock file writes in check/locked modes.
    descriptor = os.open(root, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SkillError(
                "Another skillset update is running for this root"
            ) from error
        yield
    finally:
        os.close(descriptor)


def _prepare(
    root: Path,
    manifest: Manifest,
    staged: dict[str, Path],
    original_manifest: bytes | None,
    extra: ExtraInventory | None,
    work: Path,
    copy_tree: CopyTree,
    reviewed: dict[str, str] | None,
) -> tuple[Path, Path]:
    prepared = work / "prepared"
    backups = work / "backups"
    prepared.mkdir()
    backups.mkdir()
    for name, source in staged.items():
        copy_tree(source, prepared / name)
    (prepared / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n")
    require(
        manifest_bytes(root) == original_manifest,
        "sources.json changed during publication preparation",
    )
    original: Manifest = (
        json.loads(original_manifest)
        if original_manifest is not None
        else {"schemaVersion": 2, "sources": {}}
    )
    validate_tree(root, original, extra, integrity_only=True)
    inventory = validate_tree(
        root, manifest, extra, {name: prepared / name for name in staged}
    )
    if reviewed is not None:
        require(
            set(inventory) == set(reviewed),
            "Reviewed inventory changed before publication",
        )
        require(
            all(
                digest_tree(Path(inventory[name]["path"])) == checksum
                for name, checksum in reviewed.items()
            ),
            "Scanned skill content changed before publication",
        )
    return prepared, backups


def _rollback(
    applied: list[tuple[Path, Path | None]],
    vendor_root: Path,
    created_vendor: bool,
    rename: Rename,
) -> None:
    for target, backup in reversed(applied):
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if backup is not None:
            rename(backup, target)
    if created_vendor:
        vendor_root.rmdir()
