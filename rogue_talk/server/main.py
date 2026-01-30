"""Server entry point."""

import argparse
import asyncio

from ..common.constants import DEFAULT_HOST, DEFAULT_PORT
from .game_server import GameServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Rogue-Talk Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host to bind to")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Port to bind to"
    )
    args = parser.parse_args()

    server = GameServer(args.host, args.port)
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        print("\nServer stopped")


if __name__ == "__main__":
    main()
