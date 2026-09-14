# Home Manager options

Import `homeManagerModules.default`. All options below are under `programs.skillset`.

## Skills and targets

| Option | Default | Purpose |
|---|---|---|
| `enable` | `false` | Enable validation and skill links. |
| `root` | `null` | Nix path containing `sources.json` and imported skills. |
| `skills` | `{}` | Additional skills, keyed by name. |
| `skills.<name>.path` | Required | Nix path to the complete skill directory, including `SKILL.md`. Can come from another flake or a derivation. |
| `skills.<name>.livePath` | `null` | Absolute directory to use for live editing; see below. |
| `skills.<name>.requires` | `[]` | Skill names that must exist and be selected in the same target. |
| `targets` | `{}` | Named installation destinations. |
| `targets.<id>.enable` | `true` | Install this target's selection. |
| `targets.<id>.path` | By name; see below | Directory relative to your home. |
| `targets.<id>.skills` | `[]` | List of names to install. |
| `targets.<id>.allSkills` | `false` | Install every skill declared through `root` and `skills`. |

Choose `allSkills = true` or a non-empty `skills` list, not both. An empty
selection installs nothing. New declarations reach an `allSkills` target when
you next build and apply. Disabled targets are ignored.

Names must match `SKILL.md` frontmatter: 1–64 lowercase ASCII letters, digits,
and single separating hyphens. Names must be unique across all declarations.
Dependencies are not selected automatically. All declared skills are validated,
including those not installed; failed validation stops the configuration build.

## Target path defaults

Only declared targets create links. These paths are relative to your home:

| Target | Default path | Agent docs |
|---|---|---|
| `agents` | `.agents/skills` | Shared directory |
| `codex` | `.agents/skills` | [Codex](https://learn.chatgpt.com/docs/build-skills) |
| `opencode` | `.config/opencode/skills` | [OpenCode](https://opencode.ai/v2/docs/skills) |
| `claude` | `.claude/skills` | [Claude Code](https://code.claude.com/docs/en/skills) |
| `copilot` | `.copilot/skills` | [Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills) |
| `cursor` | `.cursor/skills` | [Cursor](https://cursor.com/docs/skills) |
| `antigravity` | `.gemini/config/skills` | [Antigravity app](https://antigravity.google/docs/skills) |
| `antigravity-cli` | `.gemini/antigravity-cli/skills` | [Antigravity CLI](https://antigravity.google/docs/cli/plugins/) |
| `gemini` | `.gemini/skills` | [Gemini CLI](https://geminicli.com/docs/cli/using-agent-skills/) |
| `pi` | `.pi/agent/skills` | [Pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md) |

Set `path` to override a default or add a custom target:

```nix
programs.skillset.targets.work = {
  path = ".work-agent/skills";
  allSkills = true;
};
```

Unknown names default to `.agents/skills`. Paths must be normalized and stay
inside your home. Shell variables such as `CODEX_HOME` or `CLAUDE_CONFIG_DIR`
are not read or expanded; set a matching path yourself if your agent uses one.

`agents` and `codex` share a path: do not select the same skill in both.
Other agents may also read that shared directory. Targets control file
placement, not exclusive access. Home Manager does not force replacement of
unmanaged files at those destinations.

## Live editing

Normally, installed skills are fixed snapshots in the Nix store. For immediate
local edits, supply both a snapshot `path` and an absolute `livePath`:

```nix
programs.skillset.skills.notes = {
  path = ./skills/notes;
  livePath = "${config.home.homeDirectory}/src/my-skills/skills/notes";
};
```

```text
path only       → Nix store snapshot → changes after build and apply
path + livePath → working directory  → edits visible immediately
```

The build validates `path`; later edits in `livePath` are not covered by that
check. Declare the skill once, in `skills`, rather than also in `sources.json`.
See [Home Manager's file handling](https://nix-community.github.io/home-manager/usage/dotfiles.html)
for link behavior.

## CLI package

The module does not install the `skillset` command in your profile. Use
`nix run github:ruarfff/skillset-nix -- ...`, or add
`skillset.packages.${pkgs.stdenv.hostPlatform.system}.default` to `home.packages`
where the flake input is in scope. For scanning, select
`skillset.packages.${pkgs.stdenv.hostPlatform.system}.with-scanner` instead.
Retain skillset’s dependency pins, especially when using the scanner package.
A consumer wrapper must pass `--inventory` to include skills declared outside
`sources.json`; selecting the scanner package alone does not add them.
