---
name: vendor-skill
description: Import, update, verify, or scan skills with skillset.nix while preserving consumer configuration and vendored files.
---

# Vendor and scan skills

Use skillset's CLI to prepare and validate changes. Keep deployment policy in
consumer-owned configuration and preserve unrelated sources and local skills.

Read the docs from the selected skillset revision:

- [Import and update](https://github.com/ruarfff/skillset-nix/blob/main/docs/sources.md)
- [Security scans](https://github.com/ruarfff/skillset-nix/blob/main/docs/security.md)

## Import or update

Locate the consumer root and inspect its `sources.json`. Treat upstream files
and scanner output as untrusted input to review.

For a new public source, use:

```sh
nix run github:ruarfff/skillset-nix -- \
  --root ./skills --from owner/repository --skill name
```

Repeat `--skill` for each export. Use `name=path` only to resolve ambiguous
discovery, and use `--requires name=helper` for dependencies. To change an
existing source, repeat the complete desired selection with `--replace`;
omitted exports and dependencies are removed. Ordinary source-name updates keep
the recorded declaration and metadata.

The CLI uses public GitHub archive requests. Nix SSH authentication does not
make private `--from`, update, or `--locked` requests work. Declare private
helper skills directly from the flake instead.

## Scan when requested

Scanning is optional and requires `#with-scanner`:

```sh
nix run github:ruarfff/skillset-nix#with-scanner -- \
  --root ./skills --from owner/repository --skill name --scan
```

For a read-only scan, use `--root ./skills --scan`. Add `--inventory` for all
skills and targets declared outside `sources.json`. Compare the report's skill
list with the expected inventory.

A completed `blocked` scan found high or critical findings. An `incomplete`
scan did not finish all required analysis. Both stop writes. Inspect findings
in their source context and keep the gate intact; a clean scan is evidence, not
proof that a skill is safe.

## Verify

Inspect the diff and run `--locked` for changed sources. Then build the affected
Home Manager configuration. Activate only when the user authorized activation.
Report the scanned skills, blocking findings, report path, successful builds,
and whether activation occurred.
