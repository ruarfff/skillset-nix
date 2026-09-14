"""Turn a repository and selected skill names into a pinned import declaration."""

import re
import shutil
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from skill_inventory import (
    ImportSpec,
    JSONValue,
    Skill,
    SkillError,
    Source,
    relative_path,
    require,
    skill_name,
    valid_name,
)
from skill_upstream import Fetch, download_archive, github_api, resolve_revision


@dataclass(frozen=True)
class GitHubImport:
    repository: str
    skills: list[str]
    name: str | None = None
    ref: str | None = None
    revision: str | None = None
    license_file: str | None = None
    requires: tuple[str, ...] = ()


def _exports(
    archive: tarfile.TarFile, selectors: list[str]
) -> tuple[str, dict[str, Skill]]:
    candidates: dict[str, list[tuple[str, str]]] = {}
    for member in archive.getmembers():
        if not member.isfile() or not member.name.endswith("/SKILL.md"):
            continue
        try:
            parts = relative_path(member.name).parts
        except SkillError:
            continue
        # Read regular members directly; extractfile never resolves a link here.
        stream = archive.extractfile(member)
        require(stream is not None, f"Cannot read skill: {member.name}")
        with stream:
            name = skill_name(stream.read(65536).decode("utf-8", errors="replace"))
        if name is not None:
            candidates.setdefault(name, []).append(
                (parts[0], "/".join(parts[1:-1]) or ".")
            )
    roots = set()
    exports: dict[str, Skill] = {}
    for selector in selectors:
        name, separator, path = selector.partition("=")
        require(valid_name(name), f"Invalid skill name: {name}")
        require(name not in exports, f"Duplicate selected skill: {name}")
        if separator and path != ".":
            relative_path(path)
        matches = [
            item
            for item in candidates.get(name, [])
            if not separator or item[1] == path
        ]
        require(
            bool(matches),
            f"No skill named {name} found; use --skill {name}=path for an explicit directory",
        )
        choices = ", ".join(f"--skill {name}={item[1]}" for item in matches)
        require(len(matches) == 1, f"Ambiguous skill {name}; select one of: {choices}")
        root, path = matches[0]
        roots.add(root)
        exports[name] = {"path": path}
    require(
        len(roots) == 1, "Ambiguous source archive directories for selected exports"
    )
    return roots.pop(), exports


def _license_file(
    archive: tarfile.TarFile, root: str, exports: dict[str, Skill]
) -> str:
    names = {member.name for member in archive.getmembers()}
    own = [str(Path(skill["path"]) / "LICENSE") for skill in exports.values()]
    if all(f"{root}/{path}" in names for path in own):
        return own[0]
    candidates = sorted(
        {
            member.name.removeprefix(root + "/")
            for member in archive.getmembers()
            if member.name.startswith(root + "/")
            and re.fullmatch(
                r"(?:licen[cs]e|copying)(?:\.(?:md|txt|rst))?",
                member.name.removeprefix(root + "/"),
                re.IGNORECASE,
            )
        }
    )
    require(
        len(candidates) == 1,
        "No unique repository licence found; provide --license-file PATH ("
        + ", ".join(candidates)
        + ")",
    )
    return candidates[0]


def prepare_github(
    request: GitHubImport,
    destination: Path,
    *,
    archive: Path | None = None,
    api: Callable[[str], JSONValue] = github_api,
    fetch: Fetch = download_archive,
) -> ImportSpec:
    """Resolve once, download once, and discover exports in that exact archive."""
    repo = (
        request.repository.removeprefix("https://github.com/")
        .rstrip("/")
        .removesuffix(".git")
    )
    require(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) is not None
        and all(part not in (".", "..") for part in repo.split("/")),
        "Expected --from OWNER/REPO or https://github.com/OWNER/REPO",
    )
    name = request.name or re.sub(r"[^a-z0-9]+", "-", repo.split("/")[1].lower()).strip(
        "-"
    )
    require(valid_name(name), "Cannot derive a source name; provide --source-name NAME")
    require(bool(request.skills), "--from requires at least one --skill NAME")
    require(
        archive is None or bool(request.revision and request.ref),
        "--archive with --from requires --revision and --ref",
    )
    ref = request.ref
    if ref is None:
        metadata = api(f"repos/{repo}")
        require(isinstance(metadata, dict), f"Invalid upstream repository: {repo}")
        ref = metadata["default_branch"]
    require(
        isinstance(ref, str) and bool(ref) and not any(ord(c) < 32 for c in ref),
        "Invalid upstream ref",
    )
    source: Source = {
        "repository": "https://github.com/" + repo,
        "update": {"kind": "branch", "ref": ref},
        "revision": request.revision or "0" * 40,
        "archiveSha256": "0" * 64,
        "contentSha256": "0" * 64,
        "licenseFile": "LICENSE",
        "skills": {},
    }
    if request.revision is None:
        _, source["revision"] = resolve_revision(source, api=api)
    require(
        re.fullmatch(r"[0-9a-f]{40}", source["revision"]) is not None,
        "--revision must be a full 40-character commit ID",
    )
    if archive is None:
        fetch(source, destination)
    else:
        shutil.copyfile(archive, destination)
    with tarfile.open(destination) as contents:
        root, exports = _exports(contents, request.skills)
        license_file = request.license_file or _license_file(contents, root, exports)
    relative_path(license_file)
    for dependency in request.requires:
        skill, separator, required = dependency.partition("=")
        require(
            bool(separator) and skill in exports and valid_name(required),
            "Use --requires SKILL=DEPENDENCY with a selected skill",
        )
        exports[skill].setdefault("requires", []).append(required)
    return {
        "name": name,
        "repository": source["repository"],
        "update": source["update"],
        "revision": source["revision"],
        "licenseFile": license_file,
        "skills": exports,
    }
