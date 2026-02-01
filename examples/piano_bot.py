#!/usr/bin/env python3
"""Example bot that plays piano music continuously.

The bot:
- Stays in place (doesn't move)
- Plays piano.ogg in a loop continuously

Usage:
    python examples/piano_bot.py [--host HOST] [--port PORT] [--name NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from rogue_talk.bot import BotClient, BotConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("piano_bot")
# Silence noisy loggers
logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("aioice").setLevel(logging.WARNING)


# Path to the piano audio file (relative to this script)
PIANO_AUDIO = Path(__file__).parent / "piano.ogg"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Piano bot for rogue-talk")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=7777, help="Server port")
    parser.add_argument("--name", default="PianoBot", help="Bot display name")
    args = parser.parse_args()

    # Create the bot
    bot = BotClient(name=args.name, config=BotConfig(audio_enabled=True))

    # Connect to server
    logger.info(f"Connecting to {args.host}:{args.port} as {args.name}...")
    if not await bot.connect(args.host, args.port):
        logger.error("Failed to connect to server")
        return

    logger.info(f"Connected! Position: ({bot.x}, {bot.y})")

    # Start the music loop task
    music_task = asyncio.create_task(play_music_loop(bot))

    try:
        await bot.run()
    finally:
        music_task.cancel()
        try:
            await music_task
        except asyncio.CancelledError:
            pass


async def play_music_loop(bot: BotClient) -> None:
    """Play piano.ogg in a continuous loop."""
    if not PIANO_AUDIO.exists():
        logger.error(f"Audio file not found: {PIANO_AUDIO}")
        return

    logger.info(f"Starting continuous playback of: {PIANO_AUDIO}")

    while True:
        try:
            # Play the audio file and wait for it to finish
            await bot.speak_file(PIANO_AUDIO)
            # Small gap between loops (optional, remove for seamless loop)
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Error playing audio: {e}")
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
