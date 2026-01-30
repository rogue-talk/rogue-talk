{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    treefmt-nix.url = "github:numtide/treefmt-nix";
    treefmt-nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    {
      self,
      nixpkgs,
      treefmt-nix,
      ...
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;
          rogue-talk = python.pkgs.buildPythonApplication {
            pname = "rogue-talk";
            version = "0.1.0";
            src = ./.;
            pyproject = true;

            build-system = [ python.pkgs.setuptools ];

            dependencies = with python.pkgs; [
              blessed
              sounddevice
              opuslib
              numpy
              cryptography
            ];

            makeWrapperArgs = [
              "--prefix LD_LIBRARY_PATH : ${
                pkgs.lib.makeLibraryPath [
                  pkgs.libopus
                  pkgs.portaudio
                ]
              }"
            ];

            meta.mainProgram = "rogue-talk-client";
          };
        in
        {
          inherit rogue-talk;
          default = rogue-talk;
          client = rogue-talk.overrideAttrs { meta.mainProgram = "rogue-talk-client"; };
          server = rogue-talk.overrideAttrs { meta.mainProgram = "rogue-talk-server"; };
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;
        in
        {
          default = pkgs.mkShell {
            packages = [
              (python.withPackages (ps: [
                ps.blessed
                ps.sounddevice
                ps.opuslib
                ps.numpy
                ps.cryptography
                ps.mypy
              ]))
              pkgs.libopus
              pkgs.portaudio
            ];

            shellHook = ''
              export LD_LIBRARY_PATH="${pkgs.libopus}/lib:${pkgs.portaudio}/lib:$LD_LIBRARY_PATH"
            '';
          };
        }
      );

      formatter = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312;
          mypy = pkgs.writeShellScriptBin "mypy" ''
            exec ${
              python.withPackages (ps: [
                ps.mypy
                ps.numpy
                ps.blessed
              ])
            }/bin/mypy "$@"
          '';
        in
        (treefmt-nix.lib.evalModule pkgs {
          projectRootFile = "flake.nix";
          programs.nixfmt.enable = true;
          programs.ruff-format.enable = true;
          programs.mypy = {
            enable = true;
            package = mypy;
            directories."rogue_talk" = { };
          };
        }).config.build.wrapper
      );

      nixosModules.default =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.services.rogue-talk-server;
        in
        {
          options.services.rogue-talk-server = {
            enable = lib.mkEnableOption "rogue-talk server";
            port = lib.mkOption {
              type = lib.types.port;
              default = 7777;
              description = "Port to listen on";
            };
            openFirewall = lib.mkOption {
              type = lib.types.bool;
              default = true;
              description = "Open the firewall port";
            };
          };

          config = lib.mkIf cfg.enable {
            systemd.services.rogue-talk-server = {
              description = "Rogue-Talk Server";
              wantedBy = [ "multi-user.target" ];
              after = [ "network.target" ];
              serviceConfig = {
                ExecStart = "${
                  self.packages.${pkgs.system}.server
                }/bin/rogue-talk-server --host 0.0.0.0 --port ${toString cfg.port}";
                DynamicUser = true;
                Restart = "on-failure";
              };
            };

            networking.firewall.allowedTCPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];
            networking.firewall.allowedUDPPorts = lib.mkIf cfg.openFirewall [ cfg.port ];
          };
        };
    };
}
