# Import and update skills

Set `programs.skillset.root = ./skills;` in Home Manager. Run the commands below
from the directory that contains `skills/`. Nix supplies Python; no separate
Python setup or skillset source checkout is needed.

## Import from GitHub

Choose a public repository and skill name. No JSON declaration is needed:

```sh
mkdir -p ./skills
nix run github:ruarfff/skillset-nix -- \
  --root ./skills --from ruarfff/skillset-nix --skill vendor-skill
```

The command finds the skill and licence, pins the repository's default branch
to an exact commit, and validates the files before writing:

```text
skills/
├── sources.json                   # Generated pins and checksums
└── vendor/skillset-nix/vendor-skill/
    ├── SKILL.md
    └── LICENSE
```

Repeat `--skill` to import more skills from that repository. Names must match
`SKILL.md` frontmatter. Other sources and local skills stay unchanged.
Select the names in a Home Manager target, or use `allSkills = true`, then
build and apply your configuration. Imports do not activate skills.

For optional security scanning, use `#with-scanner` and add `--scan`:

```sh
nix run github:ruarfff/skillset-nix#with-scanner -- \
  --root ./skills --from ruarfff/skillset-nix --skill vendor-skill --scan
```

This scans the full resulting inventory, including declared personal skills,
before files change. See [scan reports and limits](security.md).

## Update and verify

Use the same command prefix with one of these operations:

```sh
nix run github:ruarfff/skillset-nix -- --root ./skills --check
```

| Operation | Result |
|---|---|
| `--check` | Report available updates; leave files unchanged. |
| `--validate` | Check local files and checksums offline; leave files unchanged. |
| `--locked` | Download pinned archives and verify they reproduce the snapshot; leave files unchanged. |
| `skillset-nix` | Update that recorded source. |
| `--all` | Update all recorded sources. |

`--check` and `--locked` also accept source names. `--check` exits 1 when
updates exist; success is 0 and errors are 2. Downloads use public GitHub
requests and are subject to its rate limits. These requests are unauthenticated;
private sources are unsupported even when Nix uses [SSH access](../README.md#private-repositories). Review updates before applying
Home Manager. New upstream skills are not added automatically.

For scanned updates, use `#with-scanner` and add `--scan` with a source name
or `--all`, as shown in the [scan guide](security.md).

If the packaged `skillset` command is installed, use it in place of
`nix run github:ruarfff/skillset-nix --`. The default root is the current directory.

## Change the imported skills

Repeat the command with **all skills to keep** and `--replace`:

```sh
nix run github:ruarfff/skillset-nix -- \
  --root ./skills --from ruarfff/skillset-nix \
  --skill vendor-skill --skill install-skillset --replace
```

Replacement requires an existing source; omitted exports are removed.
Without `--replace`, an existing source name is an error. Dependencies must
also be repeated. Duplicate names or missing dependencies stop the import.

### Generated metadata

`--from` generates the repository URL, update ref, licence-file path, selected
skill paths, explicit `--requires`, revision, and archive/content checksums.
It does not infer `scope`, groups, targets, or a licence label.

| Operation | Metadata behavior |
|---|---|
| Source-name update / `--all` | Retains declarations and extra metadata; refreshes pins and checksums. |
| `--from --replace` or reinstall | Generates a fresh source record. Old metadata and omitted dependencies are not merged. |

Other source records and local declarations stay unchanged. Keep deployment
policy in consumer-owned Nix attributes keyed by skill name, outside these
replaceable records. Consumer catalogs must handle absent optional metadata;
skillset assigns no meaning to fields such as `scope`.

## Import options

Most imports only need `--from` and `--skill`. Full GitHub HTTPS URLs work too.

| Option | When to use it |
|---|---|
| `--skill review=skills/review` | More than one directory declares the same skill name. |
| `--source-name team-skills` | Override the source name, derived from the repository name. |
| `--ref develop` | Track a branch or tag instead of the default branch. |
| `--revision FULL_COMMIT_ID` | Import a specific commit; future updates still follow the selected ref. |
| `--license-file legal/LICENSE` | Licence discovery fails or finds multiple repository licences. |
| `--requires review=helper` | Declare a dependency; repeat for more. Include dependencies in target selections. |

Discovery preserves each skill's `LICENSE`. Where one is missing, it looks for
one repository-root `LICENSE`, `LICENCE`, or `COPYING` (also `.md`, `.txt`, or
`.rst`, case-insensitive). It does not infer dependencies or licence terms.
A skill at the repository root imports that whole tree; `--skill name=.`
selects it explicitly.

For offline imports, add `--archive ./exact-commit.tar.gz`, `--revision`, and
`--ref`. This trusts the archive's association with the commit; verify it later
with `--locked`. Replacing exports at the same repository and commit still
requires the original archive checksum.

## Local skills and CLI selections

Declare local skills with [Home Manager's `skills` option](options.md), or add
`localSkills` to `sources.json`:

```json
{
  "schemaVersion": 2,
  "sources": {},
  "localSkills": {
    "helper": { "path": "local/helper" }
  }
}
```

Local paths are relative to the root, must stay outside `vendor/`, and may
include `requires`. Directories are not scanned for undeclared skills.

When the CLI needs skills or selections defined outside `sources.json`, pass
`--inventory ./inventory.json`:

```json
{
  "skills": {
    "helper": { "path": "/absolute/path/to/helper" }
  },
  "targets": {
    "agent": { "path": ".agents/skills", "skills": ["review", "helper"] }
  }
}
```

Extra skill paths must be absolute and names must be unique. Selections may
include newly imported skills. Without targets, the CLI checks dependencies
but makes no installation assumptions. Home Manager generates this validation
input itself; you do not need to duplicate its configuration in JSON.

## Validation and recovery

Imports preserve file bytes, executable bits, resources, and existing `LICENSE`
files. If an export has no `LICENSE`, the importer copies `licenseFile` into it.
Only selected trees and required licence files are extracted. Unrelated links
are ignored; links, traversal, duplicate paths, and unsupported file types in
imported paths or their ancestors are rejected.

Validation checks skill names, descriptions, declared dependencies, selected
targets, checksums, and explicit resource links under `reference/`,
`references/`, `scripts/`, and `assets/`. It does not execute scripts or assess
whether skill instructions are safe.

Imports and updates lock the root, check existing files, prepare and validate
the result, then recheck for concurrent edits before replacement. Preparation
failures leave files unchanged; write failures trigger rollback. Direct vendor
edits block updates. Keep custom changes in separately named local skills.

Replacement across directories is not crash-atomic, and external editors do
not obey the lock. Keep the root under version control. After an interrupted
write or failed rollback, inspect `sources.json`, vendor files, and retained
backups before retrying. Preserve unrelated changes. Use `--validate` to check
local integrity and `--locked` to verify reproduction.

For checksum format details, see [development notes](development.md#checksum-format).
