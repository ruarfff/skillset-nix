# Internal reader. Keep entries as a list until duplicate names are checked.
{ lib, root }:
let
  manifest = builtins.fromJSON (builtins.readFile (root + "/sources.json"));
  local = lib.mapAttrsToList (name: skill: {
    inherit name;
    path = root + "/${skill.path}";
    requires = skill.requires or [ ];
    livePath = null;
  }) (manifest.localSkills or { });
  vendor = lib.concatLists (
    lib.mapAttrsToList (
      sourceName: source:
      lib.mapAttrsToList (name: skill: {
        inherit name;
        path = root + "/vendor/${sourceName}/${name}";
        requires = skill.requires or [ ];
        livePath = null;
      }) source.skills
    ) manifest.sources
  );
in
assert lib.assertMsg (
  manifest.schemaVersion == 2
) "skillset: sources.json requires schemaVersion 2";
local ++ vendor
