import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from vexa_client.vexa import parse_url


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


REPO_ROOT = repo_root()
sys.path.insert(0, str(REPO_ROOT / "testing"))

from core import create_user_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send a bot to a meeting, print transcript segments, and append new "
            "segments to a hallucinations file."
        )
    )
    parser.add_argument("--meeting-url", required=True, help="Full meeting URL.")
    parser.add_argument("--language", default="en", help="Language code, e.g., en, ru.")
    parser.add_argument("--bot-name", default="Vexa bot", help="Bot name in the meeting.")
    parser.add_argument(
        "--output",
        help=(
            "Output file path. If relative, resolves under the hallucinations "
            "directory. Defaults to <lang>.txt in this folder."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://localhost:8056"),
        help="Vexa API base URL.",
    )
    parser.add_argument(
        "--admin-api-key",
        default=os.getenv("ADMIN_API_TOKEN"),
        help="Admin API token (used to mint a user token).",
    )
    parser.add_argument(
        "--user-api-key",
        default=os.getenv("TEST_USER_API_KEY"),
        help="User API token (used directly if provided).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds between transcript polls.",
    )
    parser.add_argument(
        "--stop-on-exit",
        action="store_true",
        default=True,
        help="Stop the bot on exit.",
    )
    parser.add_argument(
        "--no-stop-on-exit",
        action="store_false",
        dest="stop_on_exit",
        help="Do not stop the bot on exit.",
    )
    return parser.parse_args()


def resolve_output_path(output_arg: Optional[str], language: str) -> Path:
    hallucinations_dir = Path(__file__).resolve().parent
    if output_arg:
        output_path = Path(output_arg)
        if not output_path.is_absolute():
            output_path = hallucinations_dir / output_path
        return output_path
    return hallucinations_dir / f"{language}.txt"


def segment_key(segment: dict) -> str:
    for key in ("id", "segment_id", "uid"):
        if segment.get(key) is not None:
            return f"{key}:{segment.get(key)}"
    abs_start = segment.get("absolute_start_time")
    abs_end = segment.get("absolute_end_time")
    if abs_start or abs_end:
        return f"abs:{abs_start}|{abs_end}|{segment.get('text') or ''}"
    return f"rel:{segment.get('start')}|{segment.get('end')}|{segment.get('text') or ''}"


def format_segment(segment: dict) -> str:
    text = segment.get("text") or ""
    start = segment.get("absolute_start_time") or segment.get("start")
    end = segment.get("absolute_end_time") or segment.get("end")
    return f"{start} - {end} | {text}"

def normalize_text(text: str) -> str:
    return text.strip()


def format_line(text: str) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    return f" {normalized}"


def main() -> None:
    load_dotenv()
    args = parse_args()

    if not args.user_api_key and not args.admin_api_key:
        print("Error: Provide --user-api-key or --admin-api-key (or set env vars).")
        sys.exit(1)

    output_path = resolve_output_path(args.output, args.language)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_handle = open(output_path, "a", encoding="utf-8")
    output_handle.flush()
    with open(output_path, "r", encoding="utf-8") as existing_handle:
        existing_lines = existing_handle.read().splitlines()
    seen_texts = {normalize_text(line) for line in existing_lines if normalize_text(line)}

    platform, native_meeting_id, passcode = parse_url(args.meeting_url)
    client = create_user_client(
        user_api_key=args.user_api_key,
        base_url=args.base_url,
        admin_api_key=args.admin_api_key,
    )

    request_result = client.request_bot(
        platform=platform,
        native_meeting_id=native_meeting_id,
        bot_name=args.bot_name,
        language=args.language,
        task="transcribe",
        passcode=passcode,
    )

    print(f"Bot requested: {request_result}")
    print("Admit the bot to the meeting if required.")
    print(f"Appending new segments to: {output_path}")
    print("Polling transcripts. Press Ctrl+C to stop.")

    seen_segments: set[str] = set()

    try:
        while True:
            transcript = client.get_transcript(
                platform=platform,
                native_meeting_id=native_meeting_id,
            )
            segments = transcript.get("segments") or []
            print(f"Received {len(segments)} segments.")
            for segment in segments:
                print(format_segment(segment))
                key = segment_key(segment)
                if key in seen_segments:
                    continue
                seen_segments.add(key)
                text = segment.get("text") or ""
                normalized = normalize_text(text)
                if not normalized or normalized in seen_texts:
                    continue
                seen_texts.add(normalized)
                output_line = format_line(normalized)
                if not output_line:
                    continue
                output_handle.write(output_line + "\n")
                output_handle.flush()
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("Interrupted by keyboard.")
    finally:
        output_handle.close()
        if args.stop_on_exit:
            try:
                client.stop_bot(
                    platform=platform,
                    native_meeting_id=native_meeting_id,
                )
                print("Stop request sent.")
            except Exception as exc:
                print(f"Failed to stop bot: {exc}")


if __name__ == "__main__":
    main()
