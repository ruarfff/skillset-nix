"""Deterministic executable stand-in for the Cisco CLI protocol."""

import json
import sys
import time
from pathlib import Path

mode = (Path(sys.argv[0]).parent / "mode").read_text().strip()
if "--version" in sys.argv:
    print("skill-scanner 2.1.0")
    raise SystemExit(0)

skill = Path(sys.argv[2])
output = Path(sys.argv[sys.argv.index("--output") + 1])
if mode == "timeout":
    time.sleep(5)
if mode == "malformed":
    output.write_text("not JSON")
    raise SystemExit(0)
if mode == "crash":
    raise SystemExit(1)
if mode == "warning":
    print("WARNING Failed to analyze a script: fixture failure", file=sys.stderr)
if mode == "mutate":
    (skill / "SKILL.md").write_text("Changed during scanning")
if mode == "concurrent":
    personal = Path(sys.argv[0]).parent.parent / "consumer/local/personal/SKILL.md"
    personal.write_text(personal.read_text() + "Concurrent edit\n")
if mode == "swap":
    personal = Path(sys.argv[0]).parent.parent / "consumer/local/personal/SKILL.md"
    backup = Path(sys.argv[0]).parent / "personal-backup"
    if skill.name == "hello":
        backup.write_bytes(personal.read_bytes())
        personal.write_text(personal.read_text() + "Changed during scan\n")
    if skill.name == "zlast":
        personal.write_bytes(backup.read_bytes())
findings = []
if mode in ("high", "medium") or "BLOCK_SCAN" in (skill / "SKILL.md").read_text():
    findings = [
        {
            "id": "fixture-1",
            "severity": "MEDIUM" if mode == "medium" else "HIGH",
            "title": "Fixture finding",
            "file_path": "SKILL.md",
            "line_number": 5,
        }
    ]
result = {
    "skill_name": skill.name,
    "skill_path": str(skill),
    "findings": findings,
    "analyzers_used": [
        "static_analyzer",
        "bytecode",
        "pipeline",
        "correlation",
        "behavioral_analyzer",
    ],
    "analyzers_failed": [],
    "scan_metadata": {},
}
if mode == "incomplete":
    result["analyzers_failed"] = [
        {"analyzer": "behavioral_analyzer", "error": "fixture failure"}
    ]
if mode == "missing":
    result["analyzers_used"] = []
if mode == "unknown-severity":
    result["findings"] = [{"severity": "NEW"}]
if mode == "wrong-skill":
    result["skill_name"] = "someone-else"
output.write_text(json.dumps(result))
