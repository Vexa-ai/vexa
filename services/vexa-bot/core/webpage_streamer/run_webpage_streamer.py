#!/usr/bin/env python3

import argparse
import asyncio
import logging
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from webpage_streamer import WebpageStreamer


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Vexa dedicated webpage streamer")
    parser.add_argument("--video-frame-size", default="1920x1080")
    parser.add_argument("--port", type=int, default=8124)
    parser.add_argument("--display", default=os.getenv("DISPLAY", ":98"))
    parser.add_argument(
        "--pulse-monitor",
        default=os.getenv("VEXA_WEBPAGE_STREAMER_PULSE_MONITOR", "webpage_streamer_sink.monitor"),
    )
    args = parser.parse_args()

    width, height = map(int, args.video_frame_size.split("x"))
    setup_logging()
    streamer = WebpageStreamer(
        video_frame_size=(width, height),
        port=args.port,
        display_name=args.display,
        pulse_monitor_name=args.pulse_monitor,
    )
    await streamer.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
