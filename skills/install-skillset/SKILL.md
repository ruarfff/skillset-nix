---
name: install-skillset
description: Set up skillset.nix in a Nix and Home Manager configuration, declare agent targets, import skills, and verify the result.
---

# Install skillset.nix

Set up the requested skills and targets without replacing unrelated Home Manager
configuration. Read the repository's local instructions first.

Use the docs from the selected skillset revision:

- [Quick start](https://github.com/ruarfff/skillset-nix#quick-start)
- [Options](https://github.com/ruarfff/skillset-nix/blob/main/docs/options.md)
- [Imports](https://github.com/ruarfff/skillset-nix/blob/main/docs/sources.md)
- [Security scans](https://github.com/ruarfff/skillset-nix/blob/main/docs/security.md)

## Set up the flake

1. Confirm that Nix flakes and Home Manager work. The packaged CLI includes
   Python, so the user does not need a separate Python installation.
2. Add the `skillset` input and `skillset.homeManagerModules.default`. Keep the
   consumer's username, home directory, state version, and other inputs.
3. Keep skillset's own dependency pins. A consumer `nixpkgs.follows` override
   can break the optional scanner.
4. For a private fork, use
   `git+ssh://git@github.com/OWNER/skillset-nix?ref=main`. A tag is not required.
   Nix SSH access does not authenticate CLI imports from private repositories.

## Declare skills and targets

Use `programs.skillset.skills.<name>.path` for local or flake paths. Use
`programs.skillset.root` for imported snapshots. Declare each skill once.

Known target names provide default paths. Use an explicit home-relative `path`
for any other agent. Choose one selection form per target:

- `allSkills = true` installs all current and future declared skills.
- `skills = [ ... ]` installs only the listed names and their listed dependencies.

Use `livePath` only when the user requests links to mutable checkout content.

For a public GitHub repository, import with:

```sh
nix run github:ruarfff/skillset-nix -- \
  --root ./skills --from owner/repository --skill name
```

Use `#with-scanner` and `--scan` when scanning is requested. Add `--inventory`
for skills or target selections declared outside `sources.json`. Declare private
helper skills directly from `${skillset}/skills/<name>`.

## Verify

Build the affected Home Manager configuration. A successful evaluation alone
does not validate skill contents. After an import, run `--locked` with the same
skillset revision. Resolve validation, checksum, dependency, and destination
errors before completion.

Activate only when the user authorized activation. Report the selected skills,
destinations, successful builds, and whether activation occurred.
