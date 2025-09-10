#!/usr/bin/env python3
import asyncio
import json
import signal
import sys
import os
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

def clear_screen():
    """Clear the terminal screen"""
    os.system('clear' if os.name == 'posix' else 'cls')

def format_utc_time(utc_string: str) -> str:
    """Format UTC timestamp string for display"""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(utc_string.replace('Z', '+00:00'))
        return dt.strftime("%H:%M:%S")
    except:
        return utc_string

def format_time_display(segment: dict) -> str:
    """Format time display for a segment using ONLY UTC time"""
    # Only use absolute UTC time - no fallback to relative time
    start_utc = segment.get('absolute_start_time', '')
    end_utc = segment.get('absolute_end_time', '')
    
    if start_utc and end_utc:
        start_time = format_utc_time(start_utc)
        end_time = format_utc_time(end_utc)
        return f"[{start_time} - {end_time}]"
    
    # If no UTC time available, show error indicator
    return "[NO UTC TIME]"

def print_clean_transcript(segments: list, status_line: str = None):
    """Print a clean, single transcript view with UTC timestamps.
    Optionally include a status line (e.g., meeting status) at the top.
    """
    if not segments:
        return
    
    # Clear screen for clean display
    clear_screen()
    
    print(f"{Colors.HEADER}{'='*80}{Colors.END}")
    print(f"{Colors.BOLD}📝 LIVE TRANSCRIPT{Colors.END}")
    if status_line:
        print(status_line)
    print(f"{Colors.HEADER}{'='*80}{Colors.END}")
    
    # Sort segments by absolute UTC start time ONLY
    def sort_key(segment):
        abs_start = segment.get('absolute_start_time', '')
        if abs_start:
            return abs_start
        # If no UTC time, put at end with high priority
        return '9999-12-31T23:59:59+00:00'
    
    sorted_segments = sorted(segments, key=sort_key)
    
    # Group segments by speaker and combine text
    current_speaker = None
    current_text = []
    current_start_segment = None
    current_end_segment = None
    
    for segment in sorted_segments:
        speaker = segment.get('speaker', 'Unknown Speaker')
        text = clean_text(segment.get('text', ''))
        
        if speaker != current_speaker:
            # Print previous speaker's combined text
            if current_speaker and current_text:
                speaker_display = f"{Colors.CYAN}{current_speaker}{Colors.END}"
                time_range = format_time_display(current_start_segment) if current_start_segment else "[??:??:?? - ??:??:??]"
                combined_text = ' '.join(current_text)
                print(f"{speaker_display} {time_range}: {Colors.BOLD}{combined_text}{Colors.END}")
            
            # Start new speaker
            current_speaker = speaker
            current_text = [text] if text else []
            current_start_segment = segment
            current_end_segment = segment
        else:
            # Continue with same speaker
            if text:
                current_text.append(text)
            current_end_segment = segment
    
    # Print final speaker's text
    if current_speaker and current_text:
        speaker_display = f"{Colors.CYAN}{current_speaker}{Colors.END}"
        time_range = format_time_display(current_start_segment) if current_start_segment else "[??:??:?? - ??:??:??]"
        combined_text = ' '.join(current_text)
        print(f"{speaker_display} {time_range}: {Colors.BOLD}{combined_text}{Colors.END}")
    
    print(f"{Colors.HEADER}{'='*80}{Colors.END}")
    print(f"{Colors.YELLOW}Press Ctrl+C to exit{Colors.END}")

def debug_segment_fields(segments: list, event_type: str):
    """Debug function to check what fields are in WebSocket segments"""
    if not segments:
        return
    
    print(f"\n{Colors.YELLOW}🔍 DEBUG: {event_type} segment fields:{Colors.END}")
    sample_segment = segments[0]
    print(f"Available fields: {list(sample_segment.keys())}")
    
    # Check for absolute time fields
    has_absolute_start = 'absolute_start_time' in sample_segment
    has_absolute_end = 'absolute_end_time' in sample_segment
    has_relative_start = 'start' in sample_segment
    has_relative_end = 'end_time' in sample_segment
    
    print(f"absolute_start_time: {Colors.GREEN if has_absolute_start else Colors.RED}{has_absolute_start}{Colors.END}")
    print(f"absolute_end_time: {Colors.GREEN if has_absolute_end else Colors.RED}{has_absolute_end}{Colors.END}")
    print(f"start (relative): {Colors.YELLOW if has_relative_start else Colors.RED}{has_relative_start}{Colors.END}")
    print(f"end_time (relative): {Colors.YELLOW if has_relative_end else Colors.RED}{has_relative_end}{Colors.END}")
    
    if has_absolute_start:
        print(f"Sample absolute_start_time: {sample_segment.get('absolute_start_time')}")
    if has_absolute_end:
        print(f"Sample absolute_end_time: {sample_segment.get('absolute_end_time')}")
    
    # Save raw message to file
    import json
    with open(f'/tmp/ws_debug_{event_type}.json', 'w') as f:
        json.dump({
            "event_type": event_type,
            "sample_segment": sample_segment,
            "all_segments": segments[:3]  # First 3 segments
        }, f, indent=2, ensure_ascii=False)
    print(f"{Colors.CYAN}Raw message saved to: /tmp/ws_debug_{event_type}.json{Colors.END}")
    print()

def print_event(event: dict, merge_and_render=None, status_by_label=None):
    """Print or merge a WebSocket event.
    If merge_and_render is provided, it is used to accumulate and render transcript segments.
    If status_by_label (dict) is provided, track/display status transitions keyed by 'platform:native_id'.
    """
    event_type = event.get('type', 'unknown')
    payload = event.get('payload', {})
    
    if merge_and_render and event_type in ("transcript.initial", "transcript.mutable", "transcript.finalized"):
        segments = payload.get('segments', [])
        debug_segment_fields(segments, event_type)
        merge_and_render(segments)
        return
    
    if event_type == "meeting.status":
        status = payload.get('status', 'unknown')
        meeting = event.get('meeting', {}) or {}
        platform = meeting.get('platform')
        native_id = meeting.get('native_id') or meeting.get('native_meeting_id')
        meeting_label = f"{platform}:{native_id}" if platform and native_id else "unknown"

        # Track transition keyed by label if a dict was provided
        previous = None
        if isinstance(status_by_label, dict) and meeting_label != 'unknown':
            previous = status_by_label.get(meeting_label)
            status_by_label[meeting_label] = status

        # Timestamp for readability
        ts = datetime.utcnow().strftime("%H:%M:%S")

        # Persist status change regardless of rendering
        try:
            with open('/tmp/ws_status_changes.log', 'a') as f:
                f.write(json.dumps({
                    "ts": ts,
                    "meeting": meeting_label,
                    "status": status,
                    "previous": previous
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

        if previous and previous != status:
            print(f"{Colors.BOLD}[{ts}] Meeting {Colors.CYAN}{meeting_label}{Colors.END} Status:{Colors.END} {Colors.YELLOW}{previous}{Colors.END} -> {Colors.GREEN}{status}{Colors.END}")
        else:
            print(f"{Colors.BOLD}[{ts}] Meeting {Colors.CYAN}{meeting_label}{Colors.END} Status:{Colors.END} {Colors.YELLOW}{status}{Colors.END}")
    elif event_type == "subscribed":
        # Support both shapes: meetings at top-level or inside payload
        meetings = event.get('meetings') or payload.get('meetings', [])
        print(f"{Colors.GREEN}✓ Subscribed to meetings: {meetings}{Colors.END}")
    elif event_type == "pong":
        pass
    elif event_type == "error":
        error = event.get('error', 'unknown error')
        print(f"{Colors.RED}✗ Error: {error}{Colors.END}")
    else:
        print(f"{Colors.YELLOW}Unknown event type: {event_type}{Colors.END}")
        print(f"Raw payload: {json.dumps(payload, indent=2)}")


async def run(url: str, api_key: str, meetings: List[dict], ping_interval: float = 25.0, raw_mode: bool = False):
    # Add API key in both header and query to maximize compatibility
    headers = [("X-API-Key", api_key)]
    sep = '&' if '?' in url else '?'
    url_with_key = f"{url}{sep}api_key={api_key}"
    
    print(f"{Colors.BOLD}{Colors.HEADER}WebSocket Transcript Monitor{Colors.END}")
    print(f"{Colors.BOLD}Connecting to:{Colors.END} {Colors.CYAN}{url}{Colors.END}")
    print(f"{Colors.BOLD}Meetings:{Colors.END} {Colors.CYAN}{meetings}{Colors.END}")
    print(f"{Colors.BOLD}API Key:{Colors.END} {Colors.YELLOW}{api_key[:10]}...{Colors.END}")
    print()
    
    async with websockets.connect(url_with_key, extra_headers=headers, ping_interval=None) as ws:
        print(f"{Colors.GREEN}✓ Connected successfully{Colors.END}")

        # Accumulator: keep a merged transcript by absolute UTC start time
        transcript_by_abs_start = {}
        # Tracker: keep last known meeting status by 'platform:native_id'
        meeting_status_by_label = {}

        # Latest status line for display at top
        latest_status_line = None

        def merge_and_render(new_segments: list):
            # Merge by absolute_start_time only; ignore segments without UTC
            for seg in new_segments or []:
                abs_start = seg.get('absolute_start_time')
                if not abs_start:
                    continue
                # Overwrite or upsert; prefer the latest updated_at if present
                existing = transcript_by_abs_start.get(abs_start)
                if existing and existing.get('updated_at') and seg.get('updated_at'):
                    if seg['updated_at'] < existing['updated_at']:
                        continue
                transcript_by_abs_start[abs_start] = seg
            # Render full merged transcript
            merged = [transcript_by_abs_start[k] for k in sorted(transcript_by_abs_start.keys())]
            print_clean_transcript(merged, status_line=latest_status_line)

        # Subscribe to meetings using the provided format and request full transcript
        subscribe_msg = {
            "action": "subscribe",
            "meetings": meetings,
            "include_full": True
        }
        await ws.send(json.dumps(subscribe_msg))
        print(f"{Colors.GREEN}✓ Subscribed to meetings: {meetings}{Colors.END}")
        print(f"{Colors.BOLD}Waiting for transcript events...{Colors.END}\n")

        async def pinger():
            while True:
                try:
                    await asyncio.sleep(ping_interval)
                    await ws.send(json.dumps({"action": "ping"}))
                except Exception:
                    break

        raw_log_path = '/tmp/ws_raw.log'

        async def reader():
            async for frame in ws:
                try:
                    if raw_mode:
                        try:
                            with open(raw_log_path, 'a') as f:
                                f.write(frame + "\n")
                        except Exception:
                            pass
                        if frame:
                            print(f"RAW: {frame}")

                    msg = json.loads(frame)
                    # Even in raw mode, parse to identify event types
                    print_event(
                        msg,
                        merge_and_render=None if raw_mode else merge_and_render,
                        status_by_label=meeting_status_by_label,
                    )
                    # Update the latest_status_line for rendering if this is a status event
                    if isinstance(msg, dict) and msg.get('type') == 'meeting.status':
                        payload = msg.get('payload', {}) or {}
                        status = payload.get('status', 'unknown')
                        meeting = msg.get('meeting', {}) or {}
                        plat = meeting.get('platform')
                        nid = meeting.get('native_id') or meeting.get('native_meeting_id')
                        label = f"{plat}:{nid}" if plat and nid else "unknown"
                        latest_status = f"{Colors.BOLD}{Colors.YELLOW}Status:{Colors.END} {Colors.CYAN}{label}{Colors.END} → {Colors.GREEN}{status}{Colors.END}"
                        nonlocal latest_status_line
                        latest_status_line = latest_status
                except Exception:
                    print(f"{Colors.RED}Received non-JSON message: {frame}{Colors.END}")
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

    parser = argparse.ArgumentParser(description="Test Vexa API Gateway WebSocket multiplexing (native meeting ID only)")
    parser.add_argument("--url", default="ws://localhost:18056/ws", help="WebSocket URL (default: ws://localhost:18056/ws)")
    parser.add_argument("--api-key", required=True, help="API key for authentication (X-API-Key)")
    # Native meeting ID only
    parser.add_argument("--platform", required=True, help="Platform (google_meet, zoom, teams)")
    parser.add_argument("--native-id", required=True, help="Native meeting ID (platform-specific meeting code)")
    
    parser.add_argument("--ping-interval", type=float, default=25.0, help="Ping interval seconds (default: 25.0)")
    parser.add_argument("--raw", action="store_true", help="Dump raw WebSocket frames to /tmp/ws_raw.log and stdout")
    args = parser.parse_args()

    # Use native meeting id format exclusively
    meetings = [{"platform": args.platform, "native_id": args.native_id}]

    asyncio.run(run(args.url, args.api_key, meetings, args.ping_interval, raw_mode=args.raw))


if __name__ == "__main__":
    main()


