# Development

Read the [coding standards](../CODING_STANDARDS.md). From a source checkout:

```sh
nix develop
python3 -B -m unittest discover -s tests -v
nix fmt
nix flake check
nix flake check --no-build --all-systems
pre-commit run anti-slop-python --all-files
```

Native Nix checks build the package, run Python and installed CLI tests, check
Home Manager validation and links, and check formatting. The all-systems command
only evaluates configurations; it does not build or run them.

Scanner integration tests use a small fake executable to check our handling of
findings, errors, timeouts, reports, and concurrent edits. They do not run Cisco
or test its detection rules. The release workflow runs these tests and omits
the full scanner package build. Build `.#with-scanner` locally when changing
its Nix packaging; runtime dependency and Python import checks remain enabled.
Use the opt-in smoke test below when changing the scanner integration.

The [anti-slop hook](https://github.com/ruarfff/anti-slop-python) is pinned to
v0.2.0. Its first run needs network access for an isolated environment and its
supported Ruff version. Nix checks validate the hook configuration; run the
hook separately to execute it. Use `pre-commit install` only if pre-commit owns
this repository's Git hooks.

To build the example against your local changes without activation:

```sh
nix build "path:$PWD/examples#homeConfigurations.example.activationPackage" \
  --override-input skillset "path:$PWD" --no-write-lock-file --no-link
```

## Real scanner smoke test

This local check exercises our CLI against the packaged scanner: a clean
import, a blocked import that preserves files, a read-only scan, and an
incomplete timeout. It uses only temporary synthetic skills. CI keeps the fast
fake-scanner tests and does not build Cisco’s dependency tree.

```sh
scanner_package=$(nix build "path:$PWD#with-scanner" --no-link --print-out-paths)
python3 -B tests/scanner_smoke.py "$scanner_package/bin/skillset"
```

## Flake outputs

The flake pins Nixpkgs and Home Manager 26.05 and supports `aarch64-darwin`,
`x86_64-darwin`, `aarch64-linux`, and `x86_64-linux`. Consumers can supply their
own compatible inputs after testing them. The default setup retains skillset's
pins. Python source requires 3.12+; the package supplies its
own interpreter.

| Output | Purpose |
|---|---|
| `homeManagerModules.default` | Skill inventory and installation module. |
| `packages.<system>.{skillset,default}` | CLI package. |
| `apps.<system>.{skillset,default}` | `nix run` entry point. |
| `packages.<system>.with-scanner`, `apps.<system>.with-scanner` | CLI with pinned Cisco scanner on PATH. |
| `formatter.<system>` | Nixfmt and Ruff formatting. |
| `devShells.<system>.default` | Python, Ruff, Nixfmt, and pre-commit. |
| `checks.<system>.{updater,module,formatting}` | Package, module, and format checks. |

## Release tags

[The workflow](../.github/workflows/tag.yml) checks every push to `main`. It
creates a tag and GitHub Release with generated notes only when the Nix
interface, packages, Python CLI, or bundled helper skills change. Documentation,
examples, tests, and repository tooling do not create releases. Versioning
starts at `v0.1.0`, then increments the patch of the highest stable
`vMAJOR.MINOR.PATCH` tag. Prerelease and unrelated tags are ignored.

A rerun skips an existing tag and creates its missing release. Maintainers can
set a new major or minor version by tagging a commit explicitly. Repository
rules must allow the workflow's `GITHUB_TOKEN` to create `v*` tags and releases.

## Checksum format

`archiveSha256` is the lowercase SHA-256 hex digest of the downloaded archive.
`contentSha256` hashes one UTF-8 JSON record per file, sorted by relative path:
`[relativePath, executable, fileSha256]`. Records use compact separators and a
trailing newline. `executable` is true when any ordinary executable bit is set.
Timestamps and empty directories are excluded.

Imports preserve bytes and ordinary executable bits. Ownership, timestamps,
special permission bits, and archive write permissions are not guaranteed;
Nix normalizes store modes.
