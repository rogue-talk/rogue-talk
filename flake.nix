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

          # Python with ffmpeg+pulse (full ffmpeg for PulseAudio stream naming)
          python = pkgs.python312.override {
            packageOverrides = self: super: {
              av = super.av.override {
                ffmpeg-headless = pkgs.ffmpeg;
              };
              opuslib_next = self.buildPythonPackage {
                pname = "opuslib_next";
                version = "1.1.5";
                src = pkgs.fetchPypi {
                  pname = "opuslib_next";
                  version = "1.1.5";
                  sha256 = "6aec1015f25f799794d217601c74ea0fae8fd65d752578cb163fd2338ed075ce";
                };
                pyproject = true;
                build-system = [ self.poetry-core ];
                doCheck = false;
              };
            };
          };

          mkRogueTalk =
            mainProgram:
            python.pkgs.buildPythonApplication {
              pname = "rogue-talk";
              version = "0.1.0";
              src = ./.;
              pyproject = true;

              build-system = [ python.pkgs.setuptools ];

              dependencies = with python.pkgs; [
                blessed
                soundfile
                opuslib_next
                numpy
                cryptography
                aiortc
                aiohttp
              ];

              makeWrapperArgs = [
                "--prefix LD_LIBRARY_PATH : ${
                  pkgs.lib.makeLibraryPath [
                    pkgs.libopus
                    pkgs.libsndfile
                    pkgs.libvpx
                    pkgs.ffmpeg
                  ]
                }"
              ];

              meta.mainProgram = mainProgram;
            };
        in
        rec {
          default = mkRogueTalk "rogue-talk-client";
          client = mkRogueTalk "rogue-talk-client";
          server = mkRogueTalk "rogue-talk-server";
          bot-greeter =
            let
              pythonEnv = python.withPackages (ps: [
                ps.blessed
                ps.soundfile
                ps.opuslib_next
                ps.numpy
                ps.cryptography
                ps.aiortc
                ps.aiohttp
              ]);
              examples = ./examples;
            in
            pkgs.writeShellScriptBin "bot-greeter" ''
              export LD_LIBRARY_PATH="${
                pkgs.lib.makeLibraryPath [
                  pkgs.libopus
                  pkgs.libsndfile
                  pkgs.libvpx
                  pkgs.ffmpeg
                ]
              }:$LD_LIBRARY_PATH"
              export PYTHONPATH="${./.}:$PYTHONPATH"
              exec ${pythonEnv}/bin/python ${examples}/greeter_bot.py "$@"
            '';
          bot-piano =
            let
              pythonEnv = python.withPackages (ps: [
                ps.blessed
                ps.soundfile
                ps.opuslib_next
                ps.numpy
                ps.cryptography
                ps.aiortc
                ps.aiohttp
              ]);
              examples = ./examples;
            in
            pkgs.writeShellScriptBin "bot-piano" ''
              export LD_LIBRARY_PATH="${
                pkgs.lib.makeLibraryPath [
                  pkgs.libopus
                  pkgs.libsndfile
                  pkgs.libvpx
                  pkgs.ffmpeg
                ]
              }:$LD_LIBRARY_PATH"
              export PYTHONPATH="${./.}:$PYTHONPATH"
              exec ${pythonEnv}/bin/python ${examples}/piano_bot.py "$@"
            '';
        }
      );

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          # Override Python packages to use ffmpeg with PulseAudio support
          pythonWithPulse = pkgs.python312.override {
            packageOverrides = self: super: {
              av = super.av.override {
                ffmpeg-headless = pkgs.ffmpeg;
              };
              opuslib_next = self.buildPythonPackage {
                pname = "opuslib_next";
                version = "1.1.5";
                src = pkgs.fetchPypi {
                  pname = "opuslib_next";
                  version = "1.1.5";
                  sha256 = "6aec1015f25f799794d217601c74ea0fae8fd65d752578cb163fd2338ed075ce";
                };
                pyproject = true;
                build-system = [ self.poetry-core ];
                doCheck = false;
              };
            };
          };
        in
        {
          default = pkgs.mkShell {
            packages = [
              (pythonWithPulse.withPackages (ps: [
                ps.blessed
                ps.soundfile
                ps.opuslib_next
                ps.numpy
                ps.cryptography
                ps.mypy
                ps.aiortc
                ps.aiohttp
              ]))
              pkgs.libopus
              pkgs.libsndfile
              pkgs.libvpx
              pkgs.ffmpeg
            ];

            shellHook = ''
              export LD_LIBRARY_PATH="${pkgs.libopus}/lib:${pkgs.libsndfile}/lib:${pkgs.libvpx}/lib:${pkgs.ffmpeg}/lib:$LD_LIBRARY_PATH"
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
                ps.aiortc
                ps.aiohttp
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
