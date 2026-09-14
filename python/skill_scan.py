"""Scan immutable copies of declared skills with a pinned local Cisco CLI."""

import json
import math
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from skill_inventory import JSONValue, Manifest, Skill, SkillError, digest_tree, require

SCANNER_VERSION = "2.1.0"
ANALYZERS = {
    "static_analyzer",
    "bytecode",
    "pipeline",
    "correlation",
    "behavioral_analyzer",
}
SEVERITIES = {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}


@dataclass(frozen=True)
class ScanOptions:
    report: Path | None = None
    timeout: float = 120


def _run(
    command: list[str], work: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    # No inherited API keys, .env files, provider configuration, or user home.
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(work),
        "TMPDIR": str(work),
        "XDG_CACHE_HOME": str(work / "cache"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        return subprocess.run(
            command,
            cwd=work,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise SkillError("Security scan timeout; no snapshot was published") from error


def _check_result(result: dict[str, JSONValue], name: str, path: Path) -> bool:
    require(isinstance(result, dict), "Invalid scanner report object")
    require(
        result.get("skill_name") == name, f"Scanner reported the wrong skill: {name}"
    )
    require(
        result.get("skill_path") == str(path),
        f"Scanner reported the wrong path: {name}",
    )
    used = result.get("analyzers_used")
    require(
        isinstance(used, list) and all(isinstance(a, str) for a in used),
        f"Missing analyzer coverage: {name}",
    )
    require(ANALYZERS <= set(used), f"Missing analyzer coverage: {name}")
    require(
        result.get("analyzers_failed", []) == [], f"Incomplete security scan: {name}"
    )
    findings = result.get("findings")
    require(isinstance(findings, list), f"Missing scanner findings: {name}")
    counts: Counter[str] = Counter()
    for finding in findings:
        require(isinstance(finding, dict), f"Invalid scanner finding: {name}")
        severity = finding.get("severity")
        require(
            isinstance(severity, str) and severity in SEVERITIES,
            f"Unknown finding severity: {name}",
        )
        counts[severity] += 1
    summary = ", ".join(
        f"{count} {severity}" for severity, count in sorted(counts.items())
    )
    print(f"Scan {name}: {summary or 'no findings'}")
    return not (counts["HIGH"] or counts["CRITICAL"])


def _scan_one(
    executable: str,
    name: str,
    source: Path,
    work: Path,
    timeout: float,
    original_hash: str,
) -> tuple[dict[str, JSONValue], int]:
    copied = work / "skills" / name
    require(
        digest_tree(source) == original_hash,
        f"Skill changed before security scan: {name}",
    )
    shutil.copytree(source, copied, symlinks=True)
    require(
        digest_tree(copied) == original_hash,
        f"Skill changed while preparing scan: {name}",
    )
    output = work / f"{name}.json"
    process = _run(
        [
            executable,
            "scan",
            str(copied),
            "--use-behavioral",
            "--policy",
            "balanced",
            "--fail-on-severity",
            "high",
            "--format",
            "json",
            "--output",
            str(output),
        ],
        work,
        timeout,
    )
    require(
        process.returncode in (0, 1) and output.is_file(),
        f"Security scanner failed: {name}",
    )
    result = json.loads(output.read_text())
    require(digest_tree(copied) == original_hash, f"Scanner changed its input: {name}")
    require(
        digest_tree(source) == original_hash,
        f"Skill changed during security scan: {name}",
    )
    return {
        "contentSha256": original_hash,
        "path": str(source),
        "result": result,
        "diagnostics": process.stderr,
    }, process.returncode


def _report_path(
    root: Path, inventory: dict[str, Skill], requested: Path | None
) -> Path:
    path = (
        requested
        or Path(tempfile.mkdtemp(prefix="skillset-scan-report-")) / "report.json"
    )
    resolved = path.resolve()
    protected = [
        root.resolve(),
        *(Path(s["path"]).resolve() for s in inventory.values()),
    ]
    require(
        not any(resolved.is_relative_to(directory) for directory in protected),
        "Scan reports must be outside the skill root and skill directories",
    )
    require(
        not path.exists() and not path.is_symlink(),
        f"Scan report already exists: {path}",
    )
    return path


def scan_inventory(
    root: Path, manifest: Manifest, inventory: dict[str, Skill], options: ScanOptions
) -> dict[str, str]:
    """Scan the full intended inventory; findings and incomplete scans block writes."""
    require(
        math.isfinite(options.timeout) and options.timeout > 0,
        "Scan timeout must be positive and finite",
    )
    report_path = _report_path(root, inventory, options.report)
    report: dict[str, object] = {
        "schemaVersion": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "status": "incomplete",
        "scanner": f"skill-scanner {SCANNER_VERSION}",
        "policy": "balanced",
        "blockingSeverities": ["HIGH", "CRITICAL"],
        "requiredAnalyzers": sorted(ANALYZERS),
        "sources": {
            name: {
                "repository": source["repository"],
                "revision": source["revision"],
                "archiveSha256": source["archiveSha256"],
                "contentSha256": source["contentSha256"],
                "skills": source["skills"],
            }
            for name, source in manifest["sources"].items()
        },
    }
    results: dict[str, dict[str, JSONValue]] = {}
    report["skills"] = results
    # Reserve the report before scanning. Never overwrite a user file, including a symlink.
    descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        try:
            hashes = _scan_all(inventory, options, results)
            report["status"] = (
                "blocked"
                if any(entry.get("blocked") for entry in results.values())
                else "passed"
            )
        except (SkillError, OSError, ValueError, TypeError) as error:
            report["error"] = str(error)
            raise
        finally:
            json.dump(report, output, indent=2)
            output.write("\n")
            print(f"Security scan report: {report_path}")
    require(
        report["status"] == "passed",
        "Security scan blocked by HIGH/CRITICAL findings; review the report",
    )
    print(f"Scanned {len(inventory)} declared skills; no blocking findings.")
    return hashes


def _scan_all(
    inventory: dict[str, Skill],
    options: ScanOptions,
    results: dict[str, dict[str, JSONValue]],
) -> dict[str, str]:
    executable = shutil.which("skill-scanner")
    require(
        executable is not None,
        "Security scanner is missing; use nix run github:ruarfff/skillset-nix#with-scanner",
    )
    with tempfile.TemporaryDirectory(prefix="skillset-scan-") as temporary:
        work = Path(temporary)
        version = _run([executable, "--version"], work, options.timeout)
        require(
            version.returncode == 0
            and version.stdout.strip() == f"skill-scanner {SCANNER_VERSION}",
            f"Expected Cisco skill-scanner {SCANNER_VERSION}; use #with-scanner",
        )
        hashes = {
            name: digest_tree(Path(skill["path"])) for name, skill in inventory.items()
        }
        for name, skill in sorted(inventory.items()):
            entry, returncode = _scan_one(
                executable,
                name,
                Path(skill["path"]),
                work,
                options.timeout,
                hashes[name],
            )
            results[name] = entry
            # Cisco can report a per-file analyzer failure only on stderr.
            require(
                not entry["diagnostics"],
                f"Scanner diagnostics require review: {name}; see the report",
            )
            passed = _check_result(entry["result"], name, work / "skills" / name)
            require(
                returncode == 0 or not passed,
                f"Security scanner exited with an error: {name}",
            )
            entry["blocked"] = not passed
        require(
            all(
                digest_tree(Path(inventory[name]["path"])) == checksum
                for name, checksum in hashes.items()
            ),
            "Inventory changed during security scanning; retry after reviewing edits",
        )
        return hashes
