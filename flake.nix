{
  description = "Declarative agent skills and explicit snapshot updates";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-26.05-darwin";
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      home-manager,
    }:
    let
      systems = [
        "aarch64-darwin"
        "x86_64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      homeManagerModules.default = import ./modules/home-manager.nix;
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        rec {
          skillset = pkgs.callPackage ./package.nix { };
          scanner = pkgs.callPackage ./scanner-package.nix { };
          with-scanner = pkgs.writeShellApplication {
            name = "skillset";
            runtimeInputs = [ scanner ];
            text = ''
              exec ${skillset}/bin/skillset "$@"
            '';
          };
          default = skillset;
        }
      );
      apps = forAllSystems (system: rec {
        skillset = {
          type = "app";
          program = "${self.packages.${system}.skillset}/bin/skillset";
          meta.description = "Update and validate declared skill snapshots";
        };
        with-scanner = {
          type = "app";
          program = "${self.packages.${system}.with-scanner}/bin/skillset";
          meta.description = "Update skill snapshots with the pinned local security scanner available";
        };
        default = skillset;
      });
      formatter = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        pkgs.writeShellApplication {
          name = "skillset-fmt";
          runtimeInputs = [
            pkgs.nixfmt
            pkgs.ruff
          ];
          text = ''
            nixfmt flake.nix package.nix scanner-package.nix modules/*.nix tests/*.nix examples/*.nix
            ruff format python tests .github/scripts
          '';
        }
      );
      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python3
              pkgs.ruff
              pkgs.nixfmt
              pkgs.pre-commit
            ];
          };
        }
      );
      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          moduleTests = import ./tests/module.nix { inherit pkgs home-manager; };
        in
        {
          updater = self.packages.${system}.skillset;
          module = moduleTests;
          formatting =
            pkgs.runCommand "skillset-format-check"
              {
                nativeBuildInputs = [
                  pkgs.nixfmt
                  pkgs.ruff
                  pkgs.pre-commit
                ];
              }
              ''
                cd ${self}
                nixfmt --check flake.nix package.nix scanner-package.nix modules/*.nix tests/*.nix examples/*.nix
                ruff format --no-cache --check python tests .github/scripts
                ruff check --no-cache python tests .github/scripts
                PRE_COMMIT_HOME="$TMPDIR/pre-commit" pre-commit validate-config .pre-commit-config.yaml
                touch "$out"
              '';
        }
      );
    };
}
