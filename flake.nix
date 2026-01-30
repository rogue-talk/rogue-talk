{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { nixpkgs, ... }:
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

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-rfc-style);
    };
}
