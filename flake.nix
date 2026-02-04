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
              livekit =
                let
                  ffiPlatform =
                    {
                      "x86_64-linux" = {
                        name = "ffi-linux-x86_64.zip";
                        hash = "sha256-hfHCceQaf6H4mYNNpwVsIlz6ky1jBXdIdXzzYi+RPOw=";
                      };
                      "aarch64-linux" = {
                        name = "ffi-linux-arm64.zip";
                        hash = "sha256-6oBrzzOTutEvcIt3J/eHKY9IMgABIB1U65tz6yGgds4=";
                      };
                      "x86_64-darwin" = {
                        name = "ffi-macos-x86_64.zip";
                        hash = "sha256-Va9OfzVkOkneKXoBmT3nheHPK/ANXJevWpY6+r/WOZk=";
                      };
                      "aarch64-darwin" = {
                        name = "ffi-macos-arm64.zip";
                        hash = "sha256-stNRvB88UWAAHmp5XuUqyinijnbY0bnyjdd+HPcPnrM=";
                      };
                    }
                    .${system};
                  ffi = pkgs.fetchurl {
                    url = "https://github.com/livekit/rust-sdks/releases/download/rust-sdks/livekit-ffi%400.12.44/${ffiPlatform.name}";
                    hash = ffiPlatform.hash;
                  };
                  livekitSrc = pkgs.fetchFromGitHub {
                    owner = "livekit";
                    repo = "python-sdks";
                    rev = "rtc-v1.0.25";
                    hash = "sha256-O8WAwm2Dw7jgbZX8pOZ+h1KjwNvg91VgKFQZHkt0QJY=";
                  };
                in
                self.buildPythonPackage {
                  pname = "livekit";
                  version = "1.0.25";
                  pyproject = true;
                  src = "${livekitSrc}/livekit-rtc";
                  postPatch = ''
                    ${pkgs.unzip}/bin/unzip ${ffi} -d livekit/rtc/resources/
                  '';
                  build-system = [
                    self.setuptools
                    self.wheel
                    self.requests
                  ];
                  nativeBuildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
                    pkgs.autoPatchelfHook
                  ];
                  buildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
                    pkgs.stdenv.cc.cc.lib
                    pkgs.libva
                  ];
                  dependencies = [
                    self.livekit-protocol
                    self.protobuf
                    self.types-protobuf
                    self.numpy
                    self.aiofiles
                  ];
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
                av
                blessed
                soundfile
                numpy
                cryptography
                livekit-api
                livekit
                aiohttp
              ];

              makeWrapperArgs = [
                "--prefix LD_LIBRARY_PATH : ${
                  pkgs.lib.makeLibraryPath [
                    pkgs.libopus
                    pkgs.libsndfile
                    pkgs.ffmpeg
                    pkgs.libva
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
                ps.numpy
                ps.cryptography
                ps.livekit-api
                ps.livekit
                ps.aiohttp
              ]);
              examples = ./examples;
            in
            pkgs.writeShellScriptBin "bot-greeter" ''
              export LD_LIBRARY_PATH="${
                pkgs.lib.makeLibraryPath [
                  pkgs.libopus
                  pkgs.libsndfile
                  pkgs.ffmpeg
                  pkgs.libva
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
                ps.numpy
                ps.cryptography
                ps.livekit-api
                ps.livekit
                ps.aiohttp
              ]);
              examples = ./examples;
            in
            pkgs.writeShellScriptBin "bot-piano" ''
              export LD_LIBRARY_PATH="${
                pkgs.lib.makeLibraryPath [
                  pkgs.libopus
                  pkgs.libsndfile
                  pkgs.ffmpeg
                  pkgs.libva
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
              livekit =
                let
                  ffiPlatform =
                    {
                      "x86_64-linux" = {
                        name = "ffi-linux-x86_64.zip";
                        hash = "sha256-hfHCceQaf6H4mYNNpwVsIlz6ky1jBXdIdXzzYi+RPOw=";
                      };
                      "aarch64-linux" = {
                        name = "ffi-linux-arm64.zip";
                        hash = "sha256-6oBrzzOTutEvcIt3J/eHKY9IMgABIB1U65tz6yGgds4=";
                      };
                      "x86_64-darwin" = {
                        name = "ffi-macos-x86_64.zip";
                        hash = "sha256-Va9OfzVkOkneKXoBmT3nheHPK/ANXJevWpY6+r/WOZk=";
                      };
                      "aarch64-darwin" = {
                        name = "ffi-macos-arm64.zip";
                        hash = "sha256-stNRvB88UWAAHmp5XuUqyinijnbY0bnyjdd+HPcPnrM=";
                      };
                    }
                    .${system};
                  ffi = pkgs.fetchurl {
                    url = "https://github.com/livekit/rust-sdks/releases/download/rust-sdks/livekit-ffi%400.12.44/${ffiPlatform.name}";
                    hash = ffiPlatform.hash;
                  };
                  livekitSrc = pkgs.fetchFromGitHub {
                    owner = "livekit";
                    repo = "python-sdks";
                    rev = "rtc-v1.0.25";
                    hash = "sha256-O8WAwm2Dw7jgbZX8pOZ+h1KjwNvg91VgKFQZHkt0QJY=";
                  };
                in
                self.buildPythonPackage {
                  pname = "livekit";
                  version = "1.0.25";
                  pyproject = true;
                  src = "${livekitSrc}/livekit-rtc";
                  postPatch = ''
                    ${pkgs.unzip}/bin/unzip ${ffi} -d livekit/rtc/resources/
                  '';
                  build-system = [
                    self.setuptools
                    self.wheel
                    self.requests
                  ];
                  nativeBuildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
                    pkgs.autoPatchelfHook
                  ];
                  buildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
                    pkgs.stdenv.cc.cc.lib
                    pkgs.libva
                  ];
                  dependencies = [
                    self.livekit-protocol
                    self.protobuf
                    self.types-protobuf
                    self.numpy
                    self.aiofiles
                  ];
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
                ps.numpy
                ps.cryptography
                ps.mypy
                ps.livekit-api
                ps.livekit
                ps.aiohttp
                # Test dependencies
                ps.pytest
                ps.pytest-asyncio
                ps.pytest-cov
                ps.hypothesis
              ]))
              pkgs.libopus
              pkgs.libsndfile
              pkgs.ffmpeg
            ];

            shellHook = ''
              export LD_LIBRARY_PATH="${pkgs.libopus}/lib:${pkgs.libsndfile}/lib:${pkgs.ffmpeg}/lib:${pkgs.libva}/lib:$LD_LIBRARY_PATH"
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

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python312.override {
            packageOverrides = self: super: {
              av = super.av.override {
                ffmpeg-headless = pkgs.ffmpeg;
              };
              livekit =
                let
                  ffiPlatform =
                    {
                      "x86_64-linux" = {
                        name = "ffi-linux-x86_64.zip";
                        hash = "sha256-hfHCceQaf6H4mYNNpwVsIlz6ky1jBXdIdXzzYi+RPOw=";
                      };
                      "aarch64-linux" = {
                        name = "ffi-linux-arm64.zip";
                        hash = "sha256-6oBrzzOTutEvcIt3J/eHKY9IMgABIB1U65tz6yGgds4=";
                      };
                      "x86_64-darwin" = {
                        name = "ffi-macos-x86_64.zip";
                        hash = "sha256-Va9OfzVkOkneKXoBmT3nheHPK/ANXJevWpY6+r/WOZk=";
                      };
                      "aarch64-darwin" = {
                        name = "ffi-macos-arm64.zip";
                        hash = "sha256-stNRvB88UWAAHmp5XuUqyinijnbY0bnyjdd+HPcPnrM=";
                      };
                    }
                    .${system};
                  ffi = pkgs.fetchurl {
                    url = "https://github.com/livekit/rust-sdks/releases/download/rust-sdks/livekit-ffi%400.12.44/${ffiPlatform.name}";
                    hash = ffiPlatform.hash;
                  };
                  livekitSrc = pkgs.fetchFromGitHub {
                    owner = "livekit";
                    repo = "python-sdks";
                    rev = "rtc-v1.0.25";
                    hash = "sha256-O8WAwm2Dw7jgbZX8pOZ+h1KjwNvg91VgKFQZHkt0QJY=";
                  };
                in
                self.buildPythonPackage {
                  pname = "livekit";
                  version = "1.0.25";
                  pyproject = true;
                  src = "${livekitSrc}/livekit-rtc";
                  postPatch = ''
                    ${pkgs.unzip}/bin/unzip ${ffi} -d livekit/rtc/resources/
                  '';
                  build-system = [
                    self.setuptools
                    self.wheel
                    self.requests
                  ];
                  nativeBuildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
                    pkgs.autoPatchelfHook
                  ];
                  buildInputs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
                    pkgs.stdenv.cc.cc.lib
                    pkgs.libva
                  ];
                  dependencies = [
                    self.livekit-protocol
                    self.protobuf
                    self.types-protobuf
                    self.numpy
                    self.aiofiles
                  ];
                  doCheck = false;
                };
            };
          };
          mypy = pkgs.writeShellScriptBin "mypy" ''
            exec ${
              python.withPackages (ps: [
                ps.mypy
                ps.numpy
                ps.blessed
                ps.aiohttp
              ])
            }/bin/mypy "$@"
          '';
          treefmtEval = treefmt-nix.lib.evalModule pkgs {
            projectRootFile = "flake.nix";
            programs.nixfmt.enable = true;
            programs.ruff-format.enable = true;
            programs.mypy = {
              enable = true;
              package = mypy;
              directories."rogue_talk" = { };
            };
          };
          pythonEnv = python.withPackages (ps: [
            ps.blessed
            ps.soundfile
            ps.numpy
            ps.cryptography
            ps.livekit-api
            ps.livekit
            ps.aiohttp
            ps.pytest
            ps.pytest-asyncio
            ps.pytest-cov
            ps.hypothesis
          ]);
        in
        {
          formatting = treefmtEval.config.build.check self;
          pytest = pkgs.runCommand "pytest" { nativeBuildInputs = [ pythonEnv ]; } ''
            export LD_LIBRARY_PATH="${pkgs.libopus}/lib:${pkgs.libsndfile}/lib:${pkgs.ffmpeg}/lib"
            cd ${self}
            pytest
            touch $out
          '';
        }
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
            livekitKeyFile = lib.mkOption {
              type = lib.types.path;
              description = "Path to LiveKit key file (format: 'key: secret')";
            };
            livekitApiKeyFile = lib.mkOption {
              type = lib.types.path;
              description = "Path to file containing the LiveKit API key";
            };
            livekitApiSecretFile = lib.mkOption {
              type = lib.types.path;
              description = "Path to file containing the LiveKit API secret";
            };
          };

          config = lib.mkIf cfg.enable {
            services.livekit = {
              enable = true;
              openFirewall = true;
              keyFile = cfg.livekitKeyFile;
            };

            systemd.services.rogue-talk-server = {
              description = "Rogue-Talk Server";
              wantedBy = [ "multi-user.target" ];
              after = [
                "network.target"
                "livekit-server.service"
              ];
              serviceConfig = {
                ExecStart = "${
                  self.packages.${pkgs.system}.server
                }/bin/rogue-talk-server --host 0.0.0.0 --port ${toString cfg.port} --levels-dir ${self}/levels";
                DynamicUser = true;
                StateDirectory = "rogue-talk";
                WorkingDirectory = "/var/lib/rogue-talk";
                Restart = "on-failure";
                LoadCredential = [
                  "livekit-api-key:${cfg.livekitApiKeyFile}"
                  "livekit-api-secret:${cfg.livekitApiSecretFile}"
                ];
                Environment = [
                  "LIVEKIT_API_KEYFILE=%d/livekit-api-key"
                  "LIVEKIT_API_SECRETFILE=%d/livekit-api-secret"
                ];
              };
            };

            networking.firewall = lib.mkIf cfg.openFirewall {
              allowedTCPPorts = [ cfg.port ];
              allowedUDPPorts = [ cfg.port ];
            };
          };
        };
    };
}
