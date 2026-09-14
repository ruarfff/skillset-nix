"""Command-line options for skill imports, updates, validation, and scanning."""

import argparse
from pathlib import Path


def argument_parser(default_root: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import, update, and validate unmodified GitHub skill snapshots.",
        epilog="Updates stay in the checkout until Nix activation. Custom skills are never overwritten.",
    )
    parser.add_argument("sources", nargs="*", help="Source names from sources.json")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check",
        action="store_true",
        help="Report upstream changes without writing; exit 1 if stale",
    )
    modes.add_argument("--all", action="store_true", help="Update every vendor source")
    modes.add_argument(
        "--locked",
        action="store_true",
        help="Verify pinned snapshots reproduce upstream content without changing the checkout",
    )
    modes.add_argument(
        "--validate",
        action="store_true",
        help="Validate the local inventory and vendor integrity without network access",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="Snapshot root containing sources.json (default: current directory)",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        help="JSON with extra skills (absolute paths) and target selections",
    )
    modes.add_argument(
        "--import",
        dest="import_spec",
        type=Path,
        help="Import a JSON source declaration and calculate its lock checksums",
    )
    modes.add_argument(
        "--from",
        dest="repository",
        metavar="OWNER/REPO",
        help="Import named skills directly from a public GitHub repository",
    )
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        metavar="NAME[=PATH]",
        help="With --from, select a skill by name or exact directory; repeat for more skills",
    )
    parser.add_argument(
        "--source-name",
        help="With --from, override the source name (default: repository name)",
    )
    parser.add_argument(
        "--ref",
        help="With --from, track this branch or tag (default: repository default branch)",
    )
    parser.add_argument(
        "--revision", help="With --from, import this exact 40-character commit"
    )
    parser.add_argument(
        "--license-file", help="With --from, use this repository-relative licence file"
    )
    parser.add_argument(
        "--requires",
        action="append",
        default=[],
        metavar="SKILL=DEPENDENCY",
        help="With --from, declare a dependency; repeat for more dependencies",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="With --from or --import, replace an existing source's complete declaration and exports",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="With --import or --from, use a local archive; requires an exact revision (and --ref with --from)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan all declared skills, including personal skills; gate imports and updates when combined",
    )
    parser.add_argument(
        "--scan-report",
        type=Path,
        help="New JSON report file outside skill directories (default: a private temporary report)",
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=120,
        help="Scanner timeout in seconds per skill (default: 120)",
    )
    return parser
