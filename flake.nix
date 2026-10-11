{
  description = "kvn: keeps a sing-box proxy on a BlancVPN subscription alive";

  inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        kvn = pkgs.callPackage ./nix/package.nix { };
        default = kvn;
      });

      homeManagerModules = rec {
        kvn = import ./nix/hm-module.nix self;
        default = kvn;
      };

      checks = forAll (pkgs: { default = self.packages.${pkgs.stdenv.hostPlatform.system}.default; });
    };
}
