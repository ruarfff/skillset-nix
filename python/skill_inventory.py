"""Validate skill catalogs, selected targets, paths, and snapshot integrity."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal, NotRequired, TypedDict

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


type JSONValue = (
    None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
)


class Skill(TypedDict):
    path: str
    requires: NotRequired[list[str]]
    scope: NotRequired[str]


class UpdatePolicy(TypedDict):
    kind: Literal["branch", "release"]
    ref: NotRequired[str]
    tagPrefix: NotRequired[str]


class SourceDeclaration(TypedDict):
    repository: str
    update: UpdatePolicy
    licenseFile: str
    license: NotRequired[str]
    skills: dict[str, Skill]


class Source(SourceDeclaration):
    revision: str
    archiveSha256: str
    contentSha256: str
    resolvedRef: NotRequired[str]


class ImportSpec(SourceDeclaration):
    name: str
    revision: NotRequired[str]


class Manifest(TypedDict):
    schemaVersion: int
    sources: dict[str, Source]
    localSkills: NotRequired[dict[str, Skill]]


class Target(TypedDict):
    path: str
    enable: NotRequired[bool]
    skills: NotRequired[list[str]]


class ExtraInventory(TypedDict, total=False):
    skills: dict[str, Skill]
    targets: dict[str, Target]


class SkillError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SkillError(message)


def relative_path(value: str) -> PurePosixPath:
    require(isinstance(value, str), "Expected a relative path string")
    path = PurePosixPath(value)
    require(
        bool(value)
        and not path.is_absolute()
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and "\\" not in value
        and not any(ord(c) < 32 or ord(c) == 127 for c in value),
        f"Unsafe relative path: {value}",
    )
    return path


def confined_path(root: Path, value: str) -> Path:
    """Reject links in ancestors as well as the final selected directory."""
    path = root
    for part in relative_path(value).parts:
        path = path / part
        require(not path.is_symlink(), f"Symlinks are not supported: {path}")
    return path


def valid_name(name: str) -> bool:
    return (
        isinstance(name, str) and len(name) <= 64 and NAME.fullmatch(name) is not None
    )


def dependencies(record: Skill, name: str) -> list[str]:
    values = record.get("requires", [])
    require(
        isinstance(values, list) and all(valid_name(v) for v in values),
        f"Invalid dependencies: {name}",
    )
    return values


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_tree(root: Path) -> str:
    """Include relative names, executable bits, and bytes, independent of timestamps."""
    require(
        root.is_dir() and not root.is_symlink(), f"Missing or linked skill tree: {root}"
    )
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        require(
            not path.is_symlink(), f"Symlinks are not supported in skill trees: {path}"
        )
        if path.is_dir():
            continue
        require(path.is_file(), f"Unsupported skill file: {path}")
        record = [
            path.relative_to(root).as_posix(),
            bool(path.stat().st_mode & 0o111),
            digest_file(path),
        ]
        digest.update(json.dumps(record, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def skill_name(text: str) -> str | None:
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    if frontmatter is None:
        return None
    declared = re.search(r"^name:\s*([^\r\n]+)", frontmatter.group(1), re.MULTILINE)
    return declared.group(1).strip(" \"'") if declared else None


def validate_skill(path: Path, name: str) -> None:
    require(valid_name(name), f"Invalid skill name: {name}")
    digest_tree(path)
    skill_file = path / "SKILL.md"
    require(skill_file.is_file(), f"Missing SKILL.md: {path}")
    text = skill_file.read_text()
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.DOTALL)
    if frontmatter is None:
        raise SkillError(f"Missing frontmatter: {skill_file}")
    fields = frontmatter.group(1)
    require(
        skill_name(text) == name,
        f"Skill name does not match directory: {skill_file}",
    )
    description = re.search(
        r"^description:[ \t]*(\S[^\r\n]*)(?:\r?\n[ \t]+[^\r\n]+)*", fields, re.MULTILINE
    )
    if description is None:
        raise SkillError(f"Missing description: {skill_file}")
    require(
        bool(description.group(1).strip(" \"'"))
        and not description.group(1).startswith("#"),
        f"Empty description: {skill_file}",
    )
    if description.group(1) in (">", "|", ">-", "|-"):
        require("\n" in description.group(0), f"Empty description: {skill_file}")
    # Check explicit local resource links, not paths in examples for the user's project.
    for target in re.findall(
        r"\]\(((?:\./)?(?:references?|scripts|assets)/[^\s)#]+)(?:#[^)]*)?\)", text
    ):
        target = target.removeprefix("./")
        relative_path(target)
        require((path / target).is_file(), f"Missing skill resource: {path / target}")


def read_manifest(root: Path) -> Manifest:
    manifest_path = confined_path(root, "sources.json")
    return validate_manifest(json.loads(manifest_path.read_text()))


def validate_manifest(manifest: Manifest) -> Manifest:
    require(isinstance(manifest, dict), "Expected sources.json object")
    require(
        manifest.get("schemaVersion") == 2,
        "Unsupported sources.json schemaVersion; migrate local patches to local owners",
    )
    require(isinstance(manifest.get("sources"), dict), "Missing source records")
    require("patches" not in manifest, "Vendor patches are not supported")
    require(
        isinstance(manifest.get("localSkills", {}), dict), "Expected localSkills object"
    )
    for name, skill in manifest.get("localSkills", {}).items():
        require(valid_name(name), f"Invalid skill name: {name}")
        relative_path(skill["path"])
        require(
            not skill["path"].startswith("vendor/") and skill["path"] != "vendor",
            f"Local skills must be outside vendor/: {name}",
        )
        dependencies(skill, name)
    names = set()
    for source_id, source in manifest["sources"].items():
        require(
            "patches" not in source,
            f"Vendor patches are not supported: {source_id}; use local policy or a custom skill",
        )
        require(valid_name(source_id), f"Invalid source name: {source_id}")
        require(
            re.fullmatch(
                r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                source["repository"],
            )
            is not None,
            f"Expected a public GitHub repository URL: {source_id}",
        )
        require(
            re.fullmatch(r"[0-9a-f]{40}", source["revision"]) is not None,
            f"Invalid revision: {source_id}",
        )
        for field in ("archiveSha256", "contentSha256"):
            require(
                SHA256.fullmatch(source[field]) is not None,
                f"Invalid {field}: {source_id}",
            )
        policy = source["update"]
        require(
            policy["kind"] in ("branch", "release"),
            f"Invalid update policy: {source_id}",
        )
        require(
            bool(
                policy.get("ref")
                if policy["kind"] == "branch"
                else policy.get("tagPrefix")
            ),
            f"Missing update ref/prefix: {source_id}",
        )
        relative_path(source["licenseFile"])
        require(bool(source["skills"]), f"No exported skills: {source_id}")
        for name, skill in source["skills"].items():
            require(
                valid_name(name) and name not in names,
                f"Duplicate or invalid skill: {name}",
            )
            names.add(name)
            if skill["path"] != ".":
                relative_path(skill["path"])
            require("patches" not in skill, f"Vendor patches are not supported: {name}")
            dependencies(skill, name)
    return manifest


def validate_skill_files(inventory: dict[str, Skill]) -> None:
    """Check declared skill files without requiring a complete installation catalog."""
    require(isinstance(inventory, dict), "Expected a skill inventory object")
    for name, skill in inventory.items():
        require(valid_name(name), f"Invalid skill name: {name}")
        path = Path(skill["path"])
        require(path.is_absolute(), f"Inventory path must be absolute: {name}")
        validate_skill(path, name)


def validate_inventory(
    inventory: dict[str, Skill], targets: dict[str, Target] | None = None
) -> dict[str, Skill]:
    """Validate declared paths, then dependencies in each explicit selection."""
    validate_skill_files(inventory)
    for name, skill in inventory.items():
        for dependency in dependencies(skill, name):
            require(
                dependency in inventory,
                f"Missing skill dependency: {name} -> {dependency}",
            )
    require(targets is None or isinstance(targets, dict), "Expected targets object")
    destinations: list[str] = []
    for target_id, target in (targets or {}).items():
        if not target.get("enable", True):
            continue
        destination = str(relative_path(target["path"]))
        selected = target.get("skills", [])
        require(
            isinstance(selected, list) and all(valid_name(n) for n in selected),
            f"Invalid selection: {target_id}",
        )
        require(
            len(selected) == len(set(selected)),
            f"Duplicate installed names: {target_id}",
        )
        for name in selected:
            require(name in inventory, f"Missing selected skill: {name}")
            for dependency in dependencies(inventory[name], name):
                require(
                    dependency in selected,
                    f"Missing selected dependency: {target_id}: {name} -> {dependency}",
                )
            path = destination + "/" + name
            for previous in destinations:
                require(
                    path != previous
                    and not path.startswith(previous + "/")
                    and not previous.startswith(path + "/"),
                    f"Overlapping installed paths: {previous}, {path}",
                )
            destinations.append(path)
    return inventory


def validate_tree(
    root: Path,
    manifest: Manifest | None = None,
    extra: ExtraInventory | None = None,
    staged: dict[str, Path] | None = None,
    *,
    integrity_only: bool = False,
) -> dict[str, Skill]:
    """Check files and hashes; check catalog relations only for the intended result."""
    staged = staged or {}
    root = root.absolute()
    manifest = read_manifest(root) if manifest is None else manifest
    require(not root.is_symlink(), f"Linked skill root: {root}")
    inventory: dict[str, Skill] = {}
    for name, skill in manifest.get("localSkills", {}).items():
        inventory[name] = {**skill, "path": str(confined_path(root, skill["path"]))}
    vendor_root = confined_path(root, "vendor")
    actual = {p.name for p in vendor_root.iterdir()} if vendor_root.exists() else set()
    require(
        actual | set(staged) == set(manifest["sources"]),
        "Vendor directories do not match sources.json",
    )
    for source_id, source in manifest["sources"].items():
        directory = staged.get(source_id, confined_path(root, "vendor/" + source_id))
        require(directory.is_dir(), f"Missing vendor source: {source_id}")
        require(
            {p.name for p in directory.iterdir()} == set(source["skills"]),
            f"Vendor exports do not match sources.json: {source_id}",
        )
        require(
            digest_tree(directory) == source["contentSha256"],
            f"Vendor content changed: {source_id}; preserve edits outside vendor/ and restore the recorded snapshot",
        )
        for name, skill in source["skills"].items():
            require(name not in inventory, f"Duplicate skill: {name}")
            require(
                (directory / name / "LICENSE").is_file(),
                f"Missing vendor licence: {name}",
            )
            inventory[name] = {**skill, "path": str(directory / name)}
    extra = extra or {}
    for name, skill in extra.get("skills", {}).items():
        require(name not in inventory, f"Duplicate skill: {name}")
        inventory[name] = skill
    if integrity_only:
        validate_skill_files(inventory)
        return inventory
    return validate_inventory(inventory, extra.get("targets"))
