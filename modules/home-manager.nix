{
  config,
  lib,
  pkgs,
  ...
}:
let
  inherit (lib) mkOption types;
  cfg = config.programs.skillset;
  targetPaths = {
    agents = ".agents/skills";
    codex = ".agents/skills";
    opencode = ".config/opencode/skills";
    claude = ".claude/skills";
    copilot = ".copilot/skills";
    cursor = ".cursor/skills";
    antigravity = ".gemini/config/skills";
    antigravity-cli = ".gemini/antigravity-cli/skills";
    gemini = ".gemini/skills";
    pi = ".pi/agent/skills";
  };
  # Copy plain absolute path strings too; keep derivation string contexts intact.
  snapshotPath =
    path:
    if lib.isDerivation path then
      toString path
    else if builtins.isPath path then
      "${path}"
    else if builtins.hasContext path then
      path
    else
      "${/. + path}";
  package = pkgs.callPackage ../package.nix { };
  validRelative =
    path:
    path != ""
    && !lib.hasPrefix "/" path
    && !lib.hasInfix "\\" path
    && builtins.match ".*[[:cntrl:]].*" path == null
    && lib.all (
      part:
      !(builtins.elem part [
        ""
        "."
        ".."
      ])
    ) (lib.splitString "/" path);
  catalog =
    lib.optionals (cfg.root != null) (
      import ./catalog.nix {
        inherit lib;
        root = cfg.root;
      }
    )
    ++ lib.mapAttrsToList (name: skill: skill // { inherit name; }) cfg.skills;
  names = map (skill: skill.name) catalog;
  inventory = builtins.listToAttrs (
    map (skill: {
      name = skill.name;
      value = skill;
    }) catalog
  );
  targets = lib.mapAttrs (_: target: {
    inherit (target) enable path;
    skills = if target.allSkills then names else target.skills;
  }) cfg.targets;
  enabledTargets = lib.filterAttrs (_: target: target.enable) targets;
  links = lib.concatLists (
    lib.mapAttrsToList (
      _: target:
      map (name: {
        inherit name;
        destination = "${target.path}/${name}";
      }) target.skills
    ) enabledTargets
  );
  validLive =
    skill:
    skill.livePath == null
    || (
      lib.hasPrefix "/" skill.livePath
      && validRelative (lib.removePrefix "/" skill.livePath)
      && !(lib.hasPrefix "${builtins.storeDir}/" skill.livePath)
      && skill.livePath != builtins.storeDir
    );
  input = pkgs.writeText "skillset-inventory.json" (
    builtins.toJSON {
      skills = lib.mapAttrs (_: skill: {
        path = snapshotPath skill.path;
        inherit (skill) requires;
      }) cfg.skills;
      inherit targets;
    }
  );
  validation = pkgs.runCommand "skillset-validated" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    export PYTHONPATH=${package}/lib/skillset
    python3 -B - ${
      lib.escapeShellArg (if cfg.root == null then "" else snapshotPath cfg.root)
    } ${input} <<'PY'
    import json, sys
    from pathlib import Path
    from skill_inventory import validate_inventory, validate_tree
    root, input_path = sys.argv[1:]
    extra = json.loads(Path(input_path).read_text())
    if root:
        validate_tree(Path(root), extra=extra)
    else:
        validate_inventory(extra["skills"], extra["targets"])
    PY
    touch "$out"
  '';
in
{
  options.programs.skillset = {
    enable = lib.mkEnableOption "declarative agent skill installation";
    root = mkOption {
      type = types.nullOr types.path;
      default = null;
      description = "Snapshot root containing schemaVersion 2 sources.json and vendor exports. localSkills are optional. Targets control which skills are installed.";
    };
    skills = mkOption {
      default = { };
      description = "Additional consumer-owned skill inventory. Keys are installed names and must match SKILL.md frontmatter.";
      type = types.attrsOf (
        types.submodule {
          options = {
            path = mkOption {
              type = types.path;
              description = "Immutable skill directory, from a local Nix path, another flake, or a derivation. Also used to validate a live skill snapshot.";
            };
            livePath = mkOption {
              type = types.nullOr types.str;
              default = null;
              description = "Explicit absolute checkout directory to link outside the store. Its later contents are not covered by snapshot validation.";
            };
            requires = mkOption {
              type = types.listOf types.str;
              default = [ ];
              description = "Skill names that must exist and be selected in every target that selects this skill.";
            };
          };
        }
      );
    };
    targets = mkOption {
      default = { };
      description = "Named installation destinations with default paths for known agents. No targets or skills are selected automatically.";
      type = types.attrsOf (
        types.submodule (
          { name, ... }: {
            options = {
              enable = mkOption {
                type = types.bool;
                default = true;
                description = "Install this target's selection.";
              };
              path = mkOption {
                type = types.str;
                default = targetPaths.${name} or ".agents/skills";
                defaultText = lib.literalExpression "the named agent's default path, or .agents/skills for custom targets";
                description = "Normalized directory relative to home.homeDirectory. Overrides the named agent's default path. Shell environment variables are not expanded.";
              };
              skills = mkOption {
                type = types.listOf types.str;
                default = [ ];
                description = "Exact inventory names to install. Include required skills explicitly. Must be empty when allSkills is true.";
              };
              allSkills = mkOption {
                type = types.bool;
                default = false;
                description = "Install every declared skill from root and skills. New skills are included on the next configuration build and activation. Cannot be combined with a non-empty skills list.";
              };
            };
          }
        )
      );
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.all (target: !target.enable || !target.allSkills || target.skills == [ ]) (
          builtins.attrValues cfg.targets
        );
        message = "skillset: target allSkills cannot be combined with a non-empty skills list.";
      }
      {
        assertion = lib.all (skill: validLive skill) catalog;
        message = "skillset: livePath must be an explicit normalized absolute path outside the Nix store.";
      }
    ];
    home.extraDependencies = lib.optional (
      catalog != [ ] || enabledTargets != { } || cfg.root != null
    ) validation;
    home.file = builtins.listToAttrs (
      map (link: {
        name = link.destination;
        value.source =
          let
            skill = inventory.${link.name};
          in
          if skill.livePath == null then skill.path else config.lib.file.mkOutOfStoreSymlink skill.livePath;
      }) (builtins.filter (link: builtins.hasAttr link.name inventory) links)
    );
  };
}
