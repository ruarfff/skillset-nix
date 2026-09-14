"""Resolve public GitHub revisions and download pinned source archives."""

import json
import re
import shutil
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from skill_inventory import JSONValue, SkillError, Source, require


def github_api(path: str) -> JSONValue:
    request = urllib.request.Request(
        "https://api.github.com/" + path, headers={"User-Agent": "skillset-nix"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def resolve_revision(
    source: Source, *, api: Callable[[str], JSONValue] = github_api
) -> tuple[str, str]:
    repo = source["repository"].removeprefix("https://github.com/")
    policy = source["update"]
    if policy["kind"] == "branch":
        ref = policy["ref"]
    else:
        releases = api(f"repos/{repo}/releases?per_page=100")
        ref = _release_ref(releases, policy["tagPrefix"], repo)
    response = api(f"repos/{repo}/commits/{urllib.parse.quote(ref, safe='')}")
    if not isinstance(response, dict):
        raise SkillError(f"Invalid upstream commit: {repo}")
    commit = response["sha"]
    if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise SkillError(f"Invalid upstream commit: {repo}")
    return ref, commit


def _release_ref(releases: JSONValue, prefix: str, repo: str) -> str:
    if not isinstance(releases, list):
        raise SkillError(f"Invalid upstream releases: {repo}")
    pattern = re.compile(re.escape(prefix) + r"(\d+)\.(\d+)\.(\d+)\Z")
    stable: list[tuple[tuple[int, ...], str]] = []
    for release in releases:
        if not isinstance(release, dict):
            raise SkillError(f"Invalid upstream release: {repo}")
        if release["draft"] or release["prerelease"]:
            continue
        tag = release["tag_name"]
        if not isinstance(tag, str):
            raise SkillError(f"Invalid upstream release tag: {repo}")
        match = pattern.fullmatch(tag)
        if match is not None:
            stable.append((tuple(map(int, match.groups())), tag))
    require(bool(stable), f"No stable {prefix} release found: {repo}")
    return max(stable, key=lambda item: item[0])[1]


def download_archive(source: Source, destination: Path) -> None:
    repo = source["repository"].removeprefix("https://github.com/")
    request = urllib.request.Request(
        f"https://codeload.github.com/{repo}/tar.gz/{source['revision']}",
        headers={"User-Agent": "skillset-nix"},
    )
    with (
        urllib.request.urlopen(request, timeout=30) as response,
        destination.open("wb") as output,
    ):
        shutil.copyfileobj(response, output)


type Fetch = Callable[[Source, Path], None]
type Resolve = Callable[[Source], tuple[str, str]]
