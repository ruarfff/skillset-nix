"""Select the next stable release tag without changing the repository."""

import re
import subprocess
from collections.abc import Iterable


def stable_version(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", tag)
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def next_tag(tags: Iterable[str], head_tags: Iterable[str]) -> str | None:
    if any(stable_version(tag) is not None for tag in head_tags):
        return None
    versions = [version for tag in tags if (version := stable_version(tag)) is not None]
    if not versions:
        return "v0.1.0"
    major, minor, patch = max(versions)
    return f"v{major}.{minor}.{patch + 1}"


def release_plan(tags: Iterable[str], head_tags: Iterable[str]) -> tuple[str, bool]:
    tags = list(tags)
    stable_head_tags = [tag for tag in head_tags if stable_version(tag) is not None]
    if stable_head_tags:
        return max(
            stable_head_tags, key=lambda tag: stable_version(tag) or (0, 0, 0)
        ), False
    return next_tag(tags, []) or "", True


def main() -> None:
    tags = subprocess.check_output(["git", "tag", "--list"], text=True).splitlines()
    head_tags = subprocess.check_output(
        ["git", "tag", "--points-at", "HEAD"], text=True
    ).splitlines()
    tag, create_tag = release_plan(tags, head_tags)
    print(f"tag={tag}")
    print(f"create_tag={str(create_tag).lower()}")


if __name__ == "__main__":
    main()
