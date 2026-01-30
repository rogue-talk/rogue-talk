# Rogue-Talk

Proximity-based voice chat roguelike.

## Running

### Client

```bash
nix run github:Lassulus/rogue-talk#client -- --host <server-ip>
```

Options:
- `--host` - Server host (default: 127.0.0.1)
- `--port` - Server port (default: 7777)
- `--name` - Player name (default: $USER)

### Server

```bash
nix run github:Lassulus/rogue-talk#server
```

Options:
- `--host` - Host to bind to (default: 127.0.0.1)
- `--port` - Port to bind to (default: 7777)

## NixOS Module

```nix
{
  inputs.rogue-talk.url = "github:Lassulus/rogue-talk";

  outputs = { self, nixpkgs, rogue-talk, ... }: {
    nixosConfigurations.myhost = nixpkgs.lib.nixosSystem {
      modules = [
        rogue-talk.nixosModules.default
        {
          services.rogue-talk-server = {
            enable = true;
            port = 7777;         # optional, default 7777
            openFirewall = true; # optional, default true
          };
        }
      ];
    };
  };
}
```
