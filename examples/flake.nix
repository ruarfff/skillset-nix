{
  description = "Minimal skillset.nix consumer";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-26.05-darwin";
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Keep skillset's tested dependency pins, including the optional scanner.
    skillset.url = "github:ruarfff/skillset-nix";
  };
  outputs =
    {
      nixpkgs,
      home-manager,
      skillset,
      ...
    }:
    {
      homeConfigurations.example = home-manager.lib.homeManagerConfiguration {
        pkgs = nixpkgs.legacyPackages.aarch64-darwin;
        modules = [
          skillset.homeManagerModules.default
          ./home.nix
        ];
      };
    };
}
