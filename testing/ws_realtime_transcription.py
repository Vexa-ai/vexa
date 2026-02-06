#!/usr/bin/env python3
"""
Real-time WebSocket Transcription Client

Implements the algorithm described in docs/websocket.md for real-time meeting transcription.
Renders a live, grouped transcript in the terminal with proper deduplication and speaker grouping.

Usage:
    python -m testing.ws_realtime_transcription \
        --api-base http://localhost:18056 \
        --ws-url ws://localhost:18056/ws \
        --api-key $API_KEY \
        --platform google_meet \
        --native-id kzj-grsa-cqf

This script:
1. Fetches initial transcript via REST API
2. Connects to WebSocket and subscribes to meeting
3. Implements deduplication by absolute_start_time
4. Groups consecutive segments by speaker
5. Renders live transcript with timestamps
"""

import argparse
import asyncio
import json
import re
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional, Set

try:
    import httpx
except ImportError:
    print("Missing dependency: httpx. Install with: pip install httpx")
    sys.exit(1)

try:
    import websockets
except ImportError:
    print("Missing dependency: websockets. Install with: pip install websockets")
    sys.exit(1)


class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


def clean_text(text: str) -> str:
    """Clean and format text for display"""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.strip())


def format_utc_time(utc_string: str) -> str:
    """Format UTC timestamp string for display"""
    try:
        dt = datetime.fromisoformat(utc_string.replace('Z', '+00:00'))
        return dt.strftime("%H:%M:%S")
    except:
        return utc_string


def clear_screen():
    """Clear the terminal screen"""
    import os
    os.system('clear' if os.name == 'posix' else 'cls')


class TranscriptRenderer:
    """Renders transcript with speaker grouping and full re-rendering"""
    
    def __init__(self, append_only: bool = False):
        self.transcript_by_abs_start: Dict[str, dict] = {}
        self.append_only = append_only
        if append_only:
            self.printed_ids: Set[str] = set()
        self.initialized = False
        self.latest_status = None
    
    def bootstrap_from_rest(self, segments: List[dict]):
        """Bootstrap transcript from REST API response - Step 1 of the algorithm"""
        print(f"{Colors.GREEN}✓ Bootstrapping from REST API: {len(segments)} segments{Colors.END}")
        
        # Seed in-memory map keyed by absolute_start_time (algorithm step 1)
        for segment in segments:
            abs_start = segment.get('absolute_start_time')
            if abs_start and segment.get('text', '').strip():
                self.transcript_by_abs_start[abs_start] = segment
        
        print(f"{Colors.GREEN}✓ Seeded {len(self.transcript_by_abs_start)} segments with absolute timestamps{Colors.END}")
        self._render()
    
    def upsert_segments(self, segments: List[dict], event_type: str):
        """Upsert segments from WebSocket message - Step 2 of the algorithm"""
        if not segments:
            return
        
        updated_count = 0
        for segment in segments:
            abs_start = segment.get('absolute_start_time')
            if not abs_start or not segment.get('text', '').strip():
                continue
            
            # Deduplication logic: keep newer updated_at timestamp (algorithm step 2)
            existing = self.transcript_by_abs_start.get(abs_start)
            if existing and existing.get('updated_at') and segment.get('updated_at'):
                if segment['updated_at'] < existing['updated_at']:
                    continue  # Keep existing (newer)
            
            self.transcript_by_abs_start[abs_start] = segment
            updated_count += 1
        
        if updated_count > 0:
            print(f"{Colors.CYAN}📝 {event_type}: {updated_count} segments updated{Colors.END}")
            self._render()
    
    def set_status(self, status: str, meeting_label: str):
        """Update meeting status"""
        self.latest_status = f"{Colors.BOLD}{Colors.YELLOW}Status:{Colors.END} {Colors.CYAN}{meeting_label}{Colors.END} → {Colors.GREEN}{status}{Colors.END}"
        print(f"{Colors.BOLD}[{datetime.utcnow().strftime('%H:%M:%S')}] Meeting {Colors.CYAN}{meeting_label}{Colors.END} Status:{Colors.END} {Colors.GREEN}{status}{Colors.END}")
        self._render()
    
    def _render(self):
        """Render the current transcript with speaker grouping"""
        if self.append_only:
            self._render_append_only()
        else:
            self._render_full()
    
    def _render_full(self):
        """Full re-render: clear screen and show complete transcript"""
        # Clear screen and move cursor to top
        print('\033[H\033[J', end='')
        
        # Render header
        print(f"{Colors.HEADER}{'='*60}{Colors.END}")
        print(f"{Colors.BOLD}📝 LIVE TRANSCRIPT (Real-time WebSocket Transcription){Colors.END}")
        if self.latest_status:
            print(self.latest_status)
        print(f"{Colors.HEADER}{'='*60}{Colors.END}")
        
        # Sort segments by absolute start time
        sorted_segments = sorted(
            (s for s in self.transcript_by_abs_start.values() if s.get('absolute_start_time')),
            key=lambda s: s['absolute_start_time']
        )
        
        # Group consecutive segments by speaker
        groups = self._group_by_speaker(sorted_segments)
        
        # Render all groups
        for group in groups:
            start_time = format_utc_time(group['start_time'])
            end_time = format_utc_time(group['end_time'])
            speaker = group['speaker']
            text = clean_text(group['text'])
            
            print(f"{Colors.CYAN}{speaker}{Colors.END} [{Colors.BLUE}{start_time} - {end_time}{Colors.END}]: {Colors.BOLD}{text}{Colors.END}")
            print()  # Add blank line after each speaker group
    
    def _render_append_only(self):
        """Append-only rendering: only print new segments (legacy mode)"""
        if not self.initialized:
            clear_screen()
            print(f"{Colors.HEADER}{'='*60}{Colors.END}")
            print(f"{Colors.BOLD}📝 LIVE TRANSCRIPT (Real-time WebSocket Transcription){Colors.END}")
            if self.latest_status:
                print(self.latest_status)
            print(f"{Colors.HEADER}{'='*60}{Colors.END}")
            self.initialized = True
        
        # Sort segments by absolute start time
        sorted_segments = sorted(
            (s for s in self.transcript_by_abs_start.values() if s.get('absolute_start_time')),
            key=lambda s: s['absolute_start_time']
        )
        
        # Group consecutive segments by speaker
        groups = self._group_by_speaker(sorted_segments)
        
        # Print new groups (deduplicated)
        for group in groups:
            key = f"{group['start_time']}|{clean_text(group['text'])}"
            if key not in self.printed_ids:
                start_time = format_utc_time(group['start_time'])
                end_time = format_utc_time(group['end_time'])
                speaker = group['speaker']
                text = clean_text(group['text'])
                
                print(f"{Colors.CYAN}{speaker}{Colors.END} [{Colors.BLUE}{start_time} - {end_time}{Colors.END}]: {Colors.BOLD}{text}{Colors.END}")
                print()  # Add blank line after each speaker group
                self.printed_ids.add(key)
    
    def _group_by_speaker(self, segments: List[dict]) -> List[dict]:
        """Group consecutive segments by same speaker - algorithm step 4"""
        groups = []
        current_group = None
        
        for segment in segments:
            speaker = segment.get('speaker', 'Unknown')
            text = clean_text(segment.get('text', ''))
            start_time = segment['absolute_start_time']
            end_time = segment.get('absolute_end_time', start_time)
            
            if not text:
                continue
            
            if current_group and current_group['speaker'] == speaker:
                # Merge with current group
                current_group['text'] += ' ' + text
                current_group['end_time'] = end_time
            else:
                # Start new group
                if current_group:
                    groups.append(current_group)
                current_group = {
                    'speaker': speaker,
                    'text': text,
                    'start_time': start_time,
                    'end_time': end_time
                }
        
        if current_group:
            groups.append(current_group)
        
        return groups


async def fetch_rest_transcript(api_base: str, api_key: str, platform: str, native_id: str) -> List[dict]:
    """Fetch initial transcript via REST API"""
    headers = {"X-API-Key": api_key}
    url = f"{api_base}/transcripts/{platform}/{native_id}"
    
    print(f"{Colors.BOLD}📡 Fetching REST transcript from: {url}{Colors.END}")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=headers)
        
        if response.status_code != 200:
            raise Exception(f"REST API failed: HTTP {response.status_code} - {response.text}")
        
        data = response.json()
        
        # Handle response format (top-level segments only)
        segments = data.get('segments', [])
        
        print(f"{Colors.GREEN}✓ REST API response: {len(segments)} segments{Colors.END}")
        return segments


async def run_websocket_validator(api_base: str, ws_url: str, api_key: str, platform: str, native_id: str, raw_mode: bool = False, append_only: bool = False):
    """Main WebSocket validator implementation"""
    
    print(f"{Colors.BOLD}{Colors.HEADER}Real-time WebSocket Transcription Client{Colors.END}")
    print(f"{Colors.BOLD}API Base:{Colors.END} {Colors.CYAN}{api_base}{Colors.END}")
    print(f"{Colors.BOLD}WebSocket URL:{Colors.END} {Colors.CYAN}{ws_url}{Colors.END}")
    print(f"{Colors.BOLD}Platform:{Colors.END} {Colors.CYAN}{platform}{Colors.END}")
    print(f"{Colors.BOLD}Native ID:{Colors.END} {Colors.CYAN}{native_id}{Colors.END}")
    print(f"{Colors.BOLD}API Key:{Colors.END} {Colors.YELLOW}{api_key[:10]}...{Colors.END}")
    print()
    
    # Step 1: Bootstrap from REST API
    try:
        rest_segments = await fetch_rest_transcript(api_base, api_key, platform, native_id)
    except Exception as e:
        print(f"{Colors.RED}❌ REST API bootstrap failed: {e}{Colors.END}")
        return
    
    # Initialize renderer and bootstrap
    renderer = TranscriptRenderer(append_only=append_only)
    renderer.bootstrap_from_rest(rest_segments)
    print()
    
    # Step 2: Connect to WebSocket with header-only authentication
    headers = [("X-API-Key", api_key)]
    
    print(f"{Colors.BOLD}🔌 Connecting to WebSocket...{Colors.END}")
    
    try:
        async with websockets.connect(ws_url, additional_headers=headers, ping_interval=None) as ws:
            print(f"{Colors.GREEN}✓ WebSocket connected{Colors.END}")
            
            # Step 3: Subscribe to meeting for live transcript updates
            subscribe_msg = {
                "action": "subscribe",
                "meetings": [{"platform": platform, "native_id": native_id}]
            }
            
            await ws.send(json.dumps(subscribe_msg))
            print(f"{Colors.GREEN}✓ Subscribed to meeting{Colors.END}")
            print(f"{Colors.BOLD}Waiting for WebSocket messages...{Colors.END}\n")
            
            # Step 4: Process WebSocket messages
            async def pinger():
                """Send ping every 25 seconds"""
                while True:
                    try:
                        await asyncio.sleep(25.0)
                        await ws.send(json.dumps({"action": "ping"}))
                    except Exception:
                        break
            
            async def message_handler():
                """Handle incoming WebSocket messages"""
                async for frame in ws:
                    try:
                        # Raw mode: log full message structure for debugging
                        if raw_mode:
                            print(f"RAW: {frame}")
                            # Write to single persistent log file
                            import os
                            from datetime import datetime
                            # Create logs directory relative to script location
                            script_dir = os.path.dirname(os.path.abspath(__file__))
                            log_dir = os.path.join(script_dir, "logs")
                            os.makedirs(log_dir, exist_ok=True)
                            # Use single persistent log file
                            log_file = f"{log_dir}/ws_raw.log"
                            
                            with open(log_file, 'a') as f:
                                f.write(f"{datetime.now().isoformat()} - {frame}\n")
                        
                        msg = json.loads(frame)
                        event_type = msg.get('type', 'unknown')
                        payload = msg.get('payload', {})
                        meeting = msg.get('meeting', {})
                        
                        meeting_label = f"{meeting.get('platform')}:{meeting.get('native_id') or meeting.get('native_meeting_id')}"
                        
                        # Process transcript events: mutable (live updates) and finalized (completed segments)
                        if event_type in ("transcript.mutable", "transcript.finalized"):
                            segments = payload.get('segments', [])
                            renderer.upsert_segments(segments, event_type)
                        
                        elif event_type == "meeting.status":
                            status = payload.get('status', 'unknown')
                            renderer.set_status(status, meeting_label)
                        
                        elif event_type == "subscribed":
                            meetings = msg.get('meetings', [])
                            print(f"{Colors.GREEN}✓ Subscribed to meetings: {meetings}{Colors.END}")
                        
                        elif event_type == "pong":
                            pass  # Silent
                        
                        elif event_type == "error":
                            error = msg.get('error', 'unknown error')
                            print(f"{Colors.RED}✗ Error: {error}{Colors.END}")
                        
                        else:
                            print(f"{Colors.YELLOW}Unknown event type: {event_type}{Colors.END}")
                            if raw_mode:
                                print(f"Raw payload: {json.dumps(payload, indent=2)}")
                    
                    except json.JSONDecodeError:
                        print(f"{Colors.RED}Received non-JSON message: {frame}{Colors.END}")
                    except Exception as e:
                        print(f"{Colors.RED}Error processing message: {e}{Colors.END}")
            
            # Start tasks
            ping_task = asyncio.create_task(pinger())
            handler_task = asyncio.create_task(message_handler())
            
            # Graceful shutdown on SIGINT
            stop_event = asyncio.Event()
            
            def signal_handler():
                stop_event.set()
            
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, signal_handler)
                except NotImplementedError:
                    pass
            
            # Wait for shutdown signal
            await stop_event.wait()
            print(f"\n{Colors.YELLOW}Shutting down...{Colors.END}")
            
            # Cancel tasks
            ping_task.cancel()
            handler_task.cancel()
            
            try:
                await ws.close()
            except Exception:
                pass
    
    except Exception as e:
        print(f"{Colors.RED}❌ WebSocket connection failed: {e}{Colors.END}")


def main():
    parser = argparse.ArgumentParser(
        description="Real-time WebSocket Transcription Client - implements algorithm from docs/websocket.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python -m testing.ws_realtime_transcription \\
    --api-base http://localhost:18056 \\
    --ws-url ws://localhost:18056/ws \\
    --api-key $API_KEY \\
    --platform google_meet \\
    --native-id kzj-grsa-cqf

  # Debug mode (show raw frames)
  python -m testing.ws_realtime_transcription \\
    --api-base http://localhost:18056 \\
    --ws-url ws://localhost:18056/ws \\
    --api-key $API_KEY \\
    --platform google_meet \\
    --native-id kzj-grsa-cqf \\
    --raw

  # Legacy append-only mode
  python -m testing.ws_realtime_transcription \\
    --api-base http://localhost:18056 \\
    --ws-url ws://localhost:18056/ws \\
    --api-key $API_KEY \\
    --platform google_meet \\
    --native-id kzj-grsa-cqf \\
    --append-only
        """
    )
    
    parser.add_argument("--api-base", required=True, help="API base URL (e.g., http://localhost:18056)")
    parser.add_argument("--ws-url", required=True, help="WebSocket URL (e.g., ws://localhost:18056/ws)")
    parser.add_argument("--api-key", required=True, help="API key for authentication")
    parser.add_argument("--platform", required=True, help="Platform (google_meet, teams)")
    parser.add_argument("--native-id", required=True, help="Native meeting ID")
    parser.add_argument("--raw", action="store_true", help="Dump raw WebSocket frames for debugging")
    parser.add_argument("--append-only", action="store_true", help="Use append-only rendering (legacy mode, default: full re-render)")
    
    args = parser.parse_args()
    
    try:
        asyncio.run(run_websocket_validator(
            api_base=args.api_base,
            ws_url=args.ws_url,
            api_key=args.api_key,
            platform=args.platform,
            native_id=args.native_id,
            raw_mode=args.raw,
            append_only=args.append_only
        ))
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted by user{Colors.END}")
        sys.exit(130)
    except Exception as e:
        print(f"\n{Colors.RED}Error: {e}{Colors.END}")
        sys.exit(1)


if __name__ == "__main__":
    main()
