"""Client entry point."""

import argparse
import asyncio
import logging
import os

from ..common.constants import DEFAULT_HOST, DEFAULT_PORT
from .game_client import GameClient


def setup_logging(log_file: str) -> None:
    """Configure logging to file only (console would interfere with TUI)."""
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
        ],
    )
    # Suppress noisy aiortc debug logs (RTP packet spam)
    logging.getLogger("aiortc").setLevel(logging.WARNING)
    logging.getLogger("aioice").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rogue-Talk Client")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Server host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Server port")
    parser.add_argument(
        "--name", default=os.environ.get("USER", "player"), help="Player name"
    )
    parser.add_argument(
        "--log", help="Log file path (logging disabled if not specified)"
    )
    args = parser.parse_args()

    if args.log:
        setup_logging(args.log)
    else:
        # Suppress all logging output (no stderr spam during TUI)
        logging.getLogger().addHandler(logging.NullHandler())

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
