"""Update and validate unmodified GitHub skill snapshots (Python 3.12+)."""

import copy
import json
import shutil
import tarfile
import tempfile
import urllib.error
from collections.abc import Callable, Sequence
from pathlib import Path

from skill_archive import materialize
from skill_cli import argument_parser
from skill_github import GitHubImport, prepare_github
from skill_inventory import (
    ExtraInventory,
    ImportSpec,
    JSONValue,
    Manifest,
    Skill,
    SkillError,
    Source,
    digest_file,
    read_manifest,
    require,
    valid_name,
    validate_manifest,
    validate_tree,
)
from skill_publish import (
    Publisher,
    locked_root,
    manifest_bytes,
    publish,
)
from skill_scan import ScanOptions, scan_inventory
from skill_upstream import (
    Fetch,
    Resolve,
    download_archive,
    github_api,
    resolve_revision,
)

DEFAULT_ROOT = Path.cwd()


def _import_manifest(
    manifest: Manifest,
    specification: ImportSpec,
    inventory: dict[str, Skill],
    replace: bool,
    archive: Path | None,
) -> tuple[str, Manifest]:
    require(isinstance(specification, dict), "Expected an import specification object")
    require(
        set(specification)
        <= {
            "name",
            "repository",
            "update",
            "license",
            "licenseFile",
            "skills",
            "revision",
        },
        "Unsupported import fields; provide source declarations without lock checksums or patches",
    )
    name = specification["name"]
    require(valid_name(name), f"Invalid source name: {name}")
    old = manifest["sources"].get(name)
    require(
        old is None or replace,
        f"Source already exists: {name}; use --replace for a complete replacement",
    )
    require(not replace or old is not None, f"Cannot replace missing source: {name}")
    pinned = specification.get("revision")
    require(
        archive is None or pinned is not None,
        "--archive requires an explicit 40-character revision in the import specification",
    )
    source: Source = {
        "repository": specification["repository"],
        "update": copy.deepcopy(specification["update"]),
        "licenseFile": specification["licenseFile"],
        "skills": copy.deepcopy(specification["skills"]),
        "revision": specification.get("revision", "0" * 40),
        "archiveSha256": "0" * 64,
        "contentSha256": "0" * 64,
    }
    if "license" in specification:
        source["license"] = specification["license"]
    updated = copy.deepcopy(manifest)
    updated["sources"][name] = source
    validate_manifest(updated)
    owned = set(old["skills"]) if old else set()
    require(
        not (set(source["skills"]) & (set(inventory) - owned)),
        "Imported skill name collides with another source or local inventory",
    )
    return name, updated


def import_source(
    root: Path,
    specification: ImportSpec | GitHubImport,
    *,
    replace: bool = False,
    archive: Path | None = None,
    extra: ExtraInventory | None = None,
    fetch: Fetch = download_archive,
    api: Callable[[str], JSONValue] = github_api,
    resolve: Resolve = resolve_revision,
    publish: Publisher = publish,
    scan: ScanOptions | None = None,
) -> int:
    """Import a complete source declaration; replacement requires explicit opt-in."""
    with locked_root(root):
        original = manifest_bytes(root)
        manifest: Manifest = (
            read_manifest(root)
            if original is not None
            else {"schemaVersion": 2, "sources": {}}
        )
        inventory = validate_tree(root, manifest, extra, integrity_only=True)
        with tempfile.TemporaryDirectory(prefix="skill-import-") as temporary:
            work = Path(temporary)
            downloaded = work / "source.tar.gz"
            discovered = isinstance(specification, GitHubImport)
            if discovered:
                specification = prepare_github(
                    specification, downloaded, archive=archive, api=api, fetch=fetch
                )
            name, updated = _import_manifest(
                manifest, specification, inventory, replace, archive
            )
            source = updated["sources"][name]
            old = manifest["sources"].get(name)
            pinned = specification.get("revision")
            if pinned is None:
                source["resolvedRef"], source["revision"] = resolve(source)
            else:
                source["resolvedRef"] = pinned
            validate_manifest(updated)
            if not discovered:
                if archive is None:
                    fetch(source, downloaded)
                else:
                    shutil.copyfile(archive, downloaded)
            source["archiveSha256"] = digest_file(downloaded)
            if old and (old["repository"], old["revision"]) == (
                source["repository"],
                source["revision"],
            ):
                require(
                    source["archiveSha256"] == old["archiveSha256"],
                    "Source archive checksum mismatch at the existing pin",
                )
            output = work / "exports"
            source["contentSha256"] = materialize(source, downloaded, output)
            staged = {name: output}
            resulting_inventory = validate_tree(root, updated, extra, staged)
            reviewed = None
            if scan is not None:
                reviewed = scan_inventory(root, updated, resulting_inventory, scan)
            publish(root, updated, staged, original, extra, reviewed)
        print(
            f"{'Replaced' if replace else 'Imported'} {name} at {source['revision']}. Review the snapshot before activation."
        )
    return 0


def _prepare_updates(
    manifest: Manifest,
    names: list[str],
    work: Path,
    locked: bool,
    check: bool,
    fetch: Fetch,
    resolve: Resolve,
) -> tuple[Manifest, dict[str, Path], bool]:
    updated = copy.deepcopy(manifest)
    staged: dict[str, Path] = {}
    stale = False
    for name in names:
        source = updated["sources"][name]
        old = manifest["sources"][name]
        if not locked:
            source["resolvedRef"], source["revision"] = resolve(source)
        changed = source["revision"] != old["revision"]
        stale |= changed
        print(
            f"{name}: {old['revision'][:12]}"
            + (
                f" -> {source['revision'][:12]}"
                if changed
                else (" (pinned)" if locked else " (current)")
            ),
            flush=True,
        )
        if check or (not locked and not changed):
            continue
        archive = work / f"{name}.tar.gz"
        fetch(source, archive)
        if changed:
            source["archiveSha256"] = digest_file(archive)
        output = work / name
        content_hash = materialize(source, archive, output)
        if locked:
            require(
                content_hash == old["contentSha256"],
                f"Pinned source does not reproduce the recorded content: {name}",
            )
        elif changed:
            source["contentSha256"] = content_hash
            staged[name] = output
    return updated, staged, stale


def update(
    root: Path,
    names: list[str],
    *,
    locked: bool = False,
    check: bool = False,
    fetch: Fetch = download_archive,
    resolve: Resolve = resolve_revision,
    extra: ExtraInventory | None = None,
    publish: Publisher = publish,
    scan: ScanOptions | None = None,
) -> int:
    require(
        scan is None or not (check or locked),
        "--scan cannot combine with --check or --locked",
    )
    with locked_root(root):
        original = (root / "sources.json").read_bytes()
        manifest = read_manifest(root)
        validate_tree(root, manifest, extra)
        require(set(names) <= manifest["sources"].keys(), "Unknown source name")
        require(len(names) == len(set(names)), "Duplicate source names")
        with tempfile.TemporaryDirectory(prefix="skill-update-") as temporary:
            updated, staged, stale = _prepare_updates(
                manifest, names, Path(temporary), locked, check, fetch, resolve
            )
            # Detect edits made while downloads ran, including edits to other skill sources.
            validate_tree(root, manifest, extra)
            require(
                (root / "sources.json").read_bytes() == original,
                "sources.json changed during the update; retry after reviewing it",
            )
            reviewed = None
            if scan is not None:
                resulting_inventory = validate_tree(root, updated, extra, staged)
                reviewed = scan_inventory(root, updated, resulting_inventory, scan)
                require(
                    manifest_bytes(root) == original,
                    "sources.json changed during security scanning",
                )
            if staged:
                publish(root, updated, staged, original, extra, reviewed)
                print(
                    "Updated vendor snapshots. Review the diff, then activate the Nix configuration."
                )
            elif locked:
                print("All selected vendor snapshots reproduce the recorded content.")
        return 1 if check and stale else 0


def scan_current(root: Path, extra: ExtraInventory | None, options: ScanOptions) -> int:
    """Scan declared vendor and personal skills without changing the inventory."""
    with locked_root(root):
        original = manifest_bytes(root)
        manifest = (
            read_manifest(root)
            if original is not None
            else {"schemaVersion": 2, "sources": {}}
        )
        inventory = validate_tree(root, manifest, extra)
        scan_inventory(root, manifest, inventory, options)
        require(
            manifest_bytes(root) == original,
            "sources.json changed during security scanning",
        )
        validate_tree(root, manifest, extra)
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    resolve: Resolve = resolve_revision,
    api: Callable[[str], JSONValue] = github_api,
    fetch: Fetch = download_archive,
) -> int:
    parser = argument_parser(DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        require(args.scan or args.scan_report is None, "--scan-report requires --scan")
        require(
            not args.scan or not (args.check or args.locked),
            "--scan cannot combine with --check or --locked",
        )
        scan = ScanOptions(args.scan_report, args.scan_timeout) if args.scan else None
        extra = json.loads(args.inventory.read_text()) if args.inventory else None
        require(
            args.repository is not None
            or not (
                args.skill
                or args.source_name is not None
                or args.ref is not None
                or args.revision is not None
                or args.license_file is not None
                or args.requires
            ),
            "--skill, --source-name, --ref, --revision, --license-file, and --requires need --from",
        )
        if args.import_spec or args.repository is not None:
            require(not args.sources, "Imports do not accept source-name arguments")
            specification = (
                GitHubImport(
                    args.repository,
                    args.skill,
                    args.source_name,
                    args.ref,
                    args.revision,
                    args.license_file,
                    tuple(args.requires),
                )
                if args.repository is not None
                else json.loads(args.import_spec.read_text())
            )
            return import_source(
                args.root,
                specification,
                replace=args.replace,
                archive=args.archive,
                api=api,
                fetch=fetch,
                resolve=resolve,
                extra=extra,
                scan=scan,
            )
        require(
            not args.replace and args.archive is None,
            "--replace and --archive require --from or --import",
        )
        if scan is not None and not (args.sources or args.all):
            return scan_current(args.root, extra, scan)
        if args.validate:
            require(not args.sources, "--validate does not accept source names")
            inventory = validate_tree(args.root, extra=extra)
            print(f"ok - validated {len(inventory)} skills")
            return 0
        if args.all and args.sources:
            parser.error("--all cannot be combined with source names")
        if not (args.sources or args.all or args.check or args.locked):
            parser.error(
                "select --from with --skill, source names, --all, --check, --locked, or --scan"
            )
        names = args.sources or list(read_manifest(args.root)["sources"])
        return update(
            args.root,
            names,
            locked=args.locked,
            check=args.check,
            extra=extra,
            resolve=resolve,
            fetch=fetch,
            scan=scan,
        )
    except (
        SkillError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        tarfile.TarError,
        urllib.error.URLError,
    ) as error:
        parser.exit(2, f"Skill update failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
