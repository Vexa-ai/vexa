#!/usr/bin/env python3
import asyncio
import json
import signal
import sys
from typing import List
from datetime import datetime
import re

try:
    import websockets
except ImportError:
    print("Missing dependency: websockets. Install with: pip install websockets")
    sys.exit(1)

# ANSI color codes for terminal output
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'


def format_timestamp(ts_str: str) -> str:
    """Format timestamp string for display"""
    try:
        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        return dt.strftime("%H:%M:%S")
    except:
        return ts_str

def format_duration(start: float, end: float) -> str:
    """Format duration in seconds to MM:SS format"""
    duration = end - start
    minutes = int(duration // 60)
    seconds = int(duration % 60)
    return f"{minutes:02d}:{seconds:02d}"

def clean_text(text: str) -> str:
    """Clean and format text for display"""
    if not text:
        return ""
    # Remove leading/trailing whitespace
    text = text.strip()
    # Replace multiple spaces with single space
    text = re.sub(r'\s+', ' ', text)
    return text

def print_segment(segment: dict, index: int = None):
    """Print a single transcript segment in a formatted way"""
    start = segment.get('start', 0)
    end = segment.get('end_time', start)
    text = clean_text(segment.get('text', ''))
    speaker = segment.get('speaker')
    language = segment.get('language', 'unknown')
    status = segment.get('speaker_mapping_status', 'UNKNOWN')
    
    # Format speaker name
    if speaker:
        speaker_display = f"{Colors.CYAN}{speaker}{Colors.END}"
    else:
        speaker_display = f"{Colors.YELLOW}Unknown Speaker{Colors.END}"
    
    # Format status
    status_color = Colors.GREEN if status == "MAPPED" else Colors.YELLOW if status == "MULTIPLE_CONCURRENT_SPEAKERS" else Colors.RED
    status_display = f"{status_color}{status}{Colors.END}"
    
    # Format duration
    duration_display = f"{Colors.BLUE}{format_duration(start, end)}{Colors.END}"
    
    # Format time range
    time_range = f"[{start:.1f}s - {end:.1f}s]"
    
    # Print segment
    if index is not None:
        print(f"  {Colors.BOLD}{index}.{Colors.END} {duration_display} {time_range}")
    else:
        print(f"  {duration_display} {time_range}")
    
    print(f"    {speaker_display} ({status_display})")
    if text:
        print(f"    {Colors.BOLD}\"{text}\"{Colors.END}")
    print()

def print_event(event: dict):
    """Print a WebSocket event in a formatted way"""
    event_type = event.get('type', 'unknown')
    meeting_id = event.get('meeting', {}).get('id', 'unknown')
    timestamp = event.get('ts', '')
    payload = event.get('payload', {})
    
    # Print event header
    print(f"\n{Colors.HEADER}{'='*80}{Colors.END}")
    print(f"{Colors.BOLD}Event Type:{Colors.END} {Colors.GREEN}{event_type}{Colors.END}")
    print(f"{Colors.BOLD}Meeting ID:{Colors.END} {Colors.CYAN}{meeting_id}{Colors.END}")
    print(f"{Colors.BOLD}Timestamp:{Colors.END} {Colors.BLUE}{format_timestamp(timestamp)}{Colors.END}")
    print(f"{Colors.HEADER}{'='*80}{Colors.END}")
    
    # Handle different event types
    if event_type == "transcript.mutable":
        segments = payload.get('segments', [])
        print(f"{Colors.BOLD}Live Transcript Updates ({len(segments)} segments):{Colors.END}")
        for i, segment in enumerate(segments, 1):
            print_segment(segment, i)
    
    elif event_type == "transcript.finalized":
        segments = payload.get('segments', [])
        print(f"{Colors.BOLD}Finalized Transcript ({len(segments)} segments):{Colors.END}")
        for i, segment in enumerate(segments, 1):
            print_segment(segment, i)
    
    elif event_type == "meeting.status":
        status = payload.get('status', 'unknown')
        print(f"{Colors.BOLD}Meeting Status:{Colors.END} {Colors.YELLOW}{status}{Colors.END}")
    
    elif event_type == "subscribed":
        meetings = payload.get('meetings', [])
        print(f"{Colors.GREEN}✓ Subscribed to meetings: {meetings}{Colors.END}")
    
    elif event_type == "pong":
        print(f"{Colors.GREEN}✓ Connection alive{Colors.END}")
    
    elif event_type == "error":
        error = event.get('error', 'unknown error')
        print(f"{Colors.RED}✗ Error: {error}{Colors.END}")
    
    else:
        print(f"{Colors.YELLOW}Unknown event type: {event_type}{Colors.END}")
        print(f"Raw payload: {json.dumps(payload, indent=2)}")


async def run(url: str, api_key: str, meeting_ids: List[int], ping_interval: float = 25.0):
    # Add API key in both header and query to maximize compatibility
    headers = [("X-API-Key", api_key)]
    sep = '&' if '?' in url else '?'
    url_with_key = f"{url}{sep}api_key={api_key}"
    
    print(f"{Colors.BOLD}{Colors.HEADER}WebSocket Transcript Monitor{Colors.END}")
    print(f"{Colors.BOLD}Connecting to:{Colors.END} {Colors.CYAN}{url}{Colors.END}")
    print(f"{Colors.BOLD}Meeting IDs:{Colors.END} {Colors.CYAN}{meeting_ids}{Colors.END}")
    print(f"{Colors.BOLD}API Key:{Colors.END} {Colors.YELLOW}{api_key[:10]}...{Colors.END}")
    print()
    
    async with websockets.connect(url_with_key, extra_headers=headers, ping_interval=None) as ws:
        print(f"{Colors.GREEN}✓ Connected successfully{Colors.END}")

        # Subscribe to meetings by internal IDs
        subscribe_msg = {
            "action": "subscribe",
            "meetings": [{"id": int(m)} for m in meeting_ids],
        }
        await ws.send(json.dumps(subscribe_msg))
        print(f"{Colors.GREEN}✓ Subscribed to meetings: {meeting_ids}{Colors.END}")
        print(f"{Colors.BOLD}Waiting for transcript events...{Colors.END}\n")

        async def pinger():
            while True:
                try:
                    await asyncio.sleep(ping_interval)
                    await ws.send(json.dumps({"action": "ping"}))
                except Exception:
                    break

        async def reader():
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    print_event(msg)
                except Exception:
                    print(f"{Colors.RED}Received non-JSON message: {raw}{Colors.END}")
                    continue

        ping_task = asyncio.create_task(pinger())
        read_task = asyncio.create_task(reader())

        # Graceful shutdown on SIGINT/SIGTERM
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def _signal_handler():
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        await stop.wait()
        ping_task.cancel()
        read_task.cancel()
        try:
            await ws.close()
        except Exception:
            pass


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Test Vexa API Gateway WebSocket multiplexing")
    parser.add_argument("--url", default="ws://localhost:18056/ws", help="WebSocket URL (default: ws://localhost:18056/ws)")
    parser.add_argument("--api-key", required=True, help="API key for authentication (X-API-Key)")
    parser.add_argument("--meeting-id", action="append", required=True, help="Internal meeting ID to subscribe; repeatable")
    parser.add_argument("--ping-interval", type=float, default=25.0, help="Ping interval seconds (default: 25.0)")
    args = parser.parse_args()

    try:
        meeting_ids = [int(m) for m in args.meeting_id]
    except ValueError:
        print("--meeting-id must be integers (internal meeting IDs)")
        sys.exit(1)

    asyncio.run(run(args.url, args.api_key, meeting_ids, args.ping_interval))


if __name__ == "__main__":
    main()


