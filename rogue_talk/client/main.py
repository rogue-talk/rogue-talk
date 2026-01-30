"""Client entry point."""

import argparse
import asyncio
import os

from ..common.constants import DEFAULT_HOST, DEFAULT_PORT
from .game_client import GameClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Rogue-Talk Client")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Server host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Server port")
    parser.add_argument(
        "--name", default=os.environ.get("USER", "player"), help="Player name"
    )
    args = parser.parse_args()

    client = GameClient(args.host, args.port, args.name)

    async def run_client() -> None:
        if await client.connect():
            await client.run()
        else:
            print("Failed to connect to server")

    try:
        asyncio.run(run_client())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
