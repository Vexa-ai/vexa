"""Entrypoint: ``python -m discord_bot`` — what the ``discord-bot`` runtime.v1 profile execs."""

from __future__ import annotations

import asyncio
import sys

from discord_bot.bot import run


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
