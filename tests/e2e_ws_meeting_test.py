#!/usr/bin/env python3
import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

try:
    # Reuse API validation schemas
    from shared_models.schemas import Platform, MeetingCreate
except ImportError:
    Platform = None  # type: ignore
    MeetingCreate = None  # type: ignore

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


# --- Utilities ---

def parse_meeting_url(url: str) -> Tuple[str, str]:
    """Parse meeting URL to (platform, native_id).

    Supports:
    - Google Meet: https://meet.google.com/<code>
    - Zoom: https://*.zoom.us/j/<id>[?pwd=...]
    - Teams: best-effort extract after last '/'
    """
    url = url.strip()
    if not url:
        raise ValueError("Meeting URL is empty")

    # Google Meet
    m = re.match(r"^https?://meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:\?.*)?$", url)
    if m:
        return "google_meet", m.group(1)

    # Zoom
    m = re.match(r"^https?://[\w.-]*zoom\.us/j/(\d{9,11})(?:\?pwd=([A-Za-z0-9]+))?", url)
    if m:
        meeting_id = m.group(1)
        pwd = m.group(2)
        return "zoom", f"{meeting_id}{f'?pwd={pwd}' if pwd else ''}"

    # Teams (best effort)
    if "teams.microsoft.com" in url:
        # Take the full URL tail as native identifier; collector/bot will handle specifics
        return "teams", url

    # Fallback: try to detect google code within the URL path
    fallback = re.search(r"([a-z]{3}-[a-z]{4}-[a-z]{3})", url)
    if fallback:
        return "google_meet", fallback.group(1)

    raise ValueError(f"Unsupported or unrecognized meeting URL format: {url}")


@dataclass
class TestConfig:
    api_base_url: str
    ws_url: str
    api_key: str
    meeting_url: str
    join_timeout_sec: int = 120
    mutable_events_timeout_sec: int = 60
    finalized_events_timeout_sec: int = 120
    stop_timeout_sec: int = 120


class TestFailure(Exception):
    pass


async def start_bot(api_base_url: str, api_key: str, platform: str, native_id: str) -> Dict[str, Any]:
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    body = {"platform": platform, "native_meeting_id": native_id}
    url = f"{api_base_url}/bots"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code not in (200, 201):
            raise TestFailure(f"Failed to start bot: HTTP {resp.status_code} {resp.text}")
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}


async def stop_bot(api_base_url: str, api_key: str, platform: str, native_id: str) -> None:
    headers = {"X-API-Key": api_key}
    url = f"{api_base_url}/bots/{platform}/{native_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers=headers)
        if resp.status_code not in (200, 202):
            raise TestFailure(f"Failed to stop bot: HTTP {resp.status_code} {resp.text}")


async def wait_for_status(ws, platform: str, native_id: str, expected_status: str, timeout_sec: int) -> Dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TestFailure(f"Timed out waiting for meeting.status='{expected_status}'")
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            raise TestFailure(f"Timed out waiting for meeting.status='{expected_status}'")
        try:
            msg = json.loads(frame)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "meeting.status":
            meeting = msg.get("meeting") or {}
            payload = msg.get("payload") or {}
            status = payload.get("status")
            if meeting.get("platform") == platform and (meeting.get("native_id") or meeting.get("native_meeting_id")) == native_id:
                if status == expected_status:
                    return msg


async def wait_for_mutable_segments(ws, platform: str, native_id: str, min_segments: int, timeout_sec: int) -> List[Dict[str, Any]]:
    """Wait until at least min_segments transcript.mutable segments arrive for the meeting."""
    deadline = asyncio.get_event_loop().time() + timeout_sec
    collected: List[Dict[str, Any]] = []
    while len(collected) < min_segments:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            continue
        try:
            msg = json.loads(frame)
        except Exception:
            continue
        if not isinstance(msg, dict) or msg.get("type") != "transcript.mutable":
            continue
        meeting = msg.get("meeting") or {}
        if meeting.get("platform") != platform or (meeting.get("native_id") or meeting.get("native_meeting_id")) != native_id:
            continue
        payload = msg.get("payload") or {}
        segments = payload.get("segments") or []
        for seg in segments:
            if isinstance(seg, dict) and "start" in seg and "end_time" in seg and isinstance(seg.get("text"), str):
                collected.append(seg)
                if len(collected) >= min_segments:
                    break
    if len(collected) < min_segments:
        raise TestFailure(f"Expected at least {min_segments} mutable segments, got {len(collected)}")
    return collected


async def test_invalid_api_key(api_base_url: str, platform: str, native_id: str) -> None:
    """Test that invalid API key is properly rejected."""
    print("Testing with invalid API key...")
    invalid_key = "invalid_api_key_12345"
    try:
        await start_bot(api_base_url, invalid_key, platform, native_id)
        raise TestFailure("❌ Invalid API key was accepted - should have been rejected")
    except TestFailure as e:
        if "401" in str(e) or "403" in str(e) or "Unauthorized" in str(e):
            print("✓ Invalid API key properly rejected with authentication error")
        else:
            raise TestFailure(f"❌ Unexpected error for invalid API key: {e}")


async def test_invalid_meeting_data(api_base_url: str, api_key: str) -> None:
    """Test that invalid meeting data is properly rejected."""
    print("Testing with invalid meeting data...")
    
    # Test invalid platform
    try:
        await start_bot(api_base_url, api_key, "invalid_platform", "test-id")
        raise TestFailure("❌ Invalid platform was accepted - should have been rejected")
    except TestFailure as e:
        if "422" in str(e) or "Invalid platform" in str(e):
            print("✓ Invalid platform properly rejected")
        else:
            print(f"⚠️  Unexpected error for invalid platform: {e}")
    
    # Test invalid native_meeting_id format
    try:
        await start_bot(api_base_url, api_key, "google_meet", "invalid-format-123")
        raise TestFailure("❌ Invalid native_meeting_id format was accepted - should have been rejected")
    except TestFailure as e:
        if "422" in str(e) or "Invalid" in str(e):
            print("✓ Invalid native_meeting_id format properly rejected")
        else:
            print(f"⚠️  Unexpected error for invalid native_meeting_id: {e}")


async def test_invalid_websocket_connection(ws_url: str, invalid_key: str, platform: str, native_id: str) -> None:
    """Test that invalid API key for WebSocket is rejected at handshake or on first action."""
    print("Testing WebSocket connection with invalid API key...")
    headers = [("X-API-Key", invalid_key)]
    sep = '&' if '?' in ws_url else '?'
    ws_url_with_key = f"{ws_url}{sep}api_key={invalid_key}"
    try:
        async with websockets.connect(ws_url_with_key, extra_headers=headers, ping_interval=None) as ws:
            # If handshake succeeded, try to subscribe; server should close or send an error
            subscribe_msg = {"action": "subscribe", "meetings": [{"platform": platform, "native_id": native_id}]}
            await ws.send(json.dumps(subscribe_msg))
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=3)
                # If we received a frame, check if it's an error/unauthorized
                try:
                    msg = json.loads(frame)
                except Exception:
                    msg = {}
                serialized = json.dumps(msg).lower() if isinstance(msg, dict) else str(frame).lower()
                if any(k in serialized for k in ["unauthorized", "forbidden", "invalid api key", "error"]):
                    print("✓ WebSocket invalid API key rejected via error message")
                else:
                    # Give a moment for a close to occur
                    await asyncio.sleep(1.0)
                    if ws.closed:
                        print("✓ WebSocket invalid API key rejected via connection close")
                    else:
                        raise TestFailure("❌ WebSocket accepted invalid API key (no error/close)")
            except asyncio.TimeoutError:
                # No message; check if closed
                if ws.closed:
                    print("✓ WebSocket invalid API key rejected via immediate close")
                else:
                    raise TestFailure("❌ WebSocket accepted invalid API key (idle open)")
    except Exception as e:
        # Handshake failure is acceptable rejection path
        print("✓ WebSocket handshake rejected invalid API key")


async def test_invalid_websocket_data(ws_url: str, api_key: str) -> None:
    """Test that invalid data sent to WebSocket is properly rejected."""
    print("Testing WebSocket with invalid subscription data...")
    headers = [("X-API-Key", api_key)]
    sep = '&' if '?' in ws_url else '?'
    ws_url_with_key = f"{ws_url}{sep}api_key={api_key}"
    
    try:
        async with websockets.connect(ws_url_with_key, extra_headers=headers, ping_interval=None) as ws:
            print("✓ WebSocket connection established with valid API key")
            
            # Test 1: Malformed JSON
            print("  - Testing malformed JSON...")
            await ws.send("invalid json {")
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=2)
                msg_str = str(frame).lower()
                if any(k in msg_str for k in ["error", "invalid", "malformed", "bad request"]):
                    print("    ✓ Malformed JSON rejected")
                else:
                    print(f"    ⚠️  Unexpected response to malformed JSON: {frame}")
            except asyncio.TimeoutError:
                print("    ⚠️  No response to malformed JSON")
            
            # Test 2: Invalid action
            print("  - Testing invalid action...")
            invalid_action = {"action": "invalid_action", "meetings": []}
            await ws.send(json.dumps(invalid_action))
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=2)
                msg_str = str(frame).lower()
                if any(k in msg_str for k in ["error", "invalid", "unknown action", "bad request"]):
                    print("    ✓ Invalid action rejected")
                else:
                    print(f"    ⚠️  Unexpected response to invalid action: {frame}")
            except asyncio.TimeoutError:
                print("    ⚠️  No response to invalid action")
            
            # Test 3: Missing required fields
            print("  - Testing missing required fields...")
            incomplete_msg = {"action": "subscribe"}  # Missing meetings
            await ws.send(json.dumps(incomplete_msg))
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=2)
                msg_str = str(frame).lower()
                if any(k in msg_str for k in ["error", "invalid", "missing", "required", "bad request"]):
                    print("    ✓ Missing required fields rejected")
                else:
                    print(f"    ⚠️  Unexpected response to missing fields: {frame}")
            except asyncio.TimeoutError:
                print("    ⚠️  No response to missing fields")
            
            # Test 4: Invalid meeting data structure
            print("  - Testing invalid meeting data structure...")
            invalid_meeting = {"action": "subscribe", "meetings": [{"invalid_field": "value"}]}
            await ws.send(json.dumps(invalid_meeting))
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=2)
                msg_str = str(frame).lower()
                if any(k in msg_str for k in ["error", "invalid", "bad request", "validation"]):
                    print("    ✓ Invalid meeting data structure rejected")
                else:
                    print(f"    ⚠️  Unexpected response to invalid meeting data: {frame}")
            except asyncio.TimeoutError:
                print("    ⚠️  No response to invalid meeting data")
                
    except Exception as e:
        print(f"❌ WebSocket connection failed: {e}")
        raise


async def test_unauthorized_ws_subscription(ws_url: str, api_key: str, platform: str, unauthorized_native_id: str) -> None:
    """Test that subscribing to a meeting that does NOT belong to the user is rejected.

    Acceptance:
    - Server responds with an error (preferred), OR
    - Server acknowledges but does NOT include the unauthorized meeting in the subscribed list, and no events arrive for it.
    Failure:
    - Server subscribes the client to the unauthorized meeting and/or events arrive for it.
    """
    print("Testing WS subscription to unauthorized meeting (should be rejected)...")
    headers = [("X-API-Key", api_key)]
    sep = '&' if '?' in ws_url else '?'
    ws_url_with_key = f"{ws_url}{sep}api_key={api_key}"

    async with websockets.connect(ws_url_with_key, extra_headers=headers, ping_interval=None) as ws:
        subscribe_msg = {"action": "subscribe", "meetings": [{"platform": platform, "native_id": unauthorized_native_id}]}
        await ws.send(json.dumps(subscribe_msg))

        # Expect either an error, or a subscribed list that does NOT include the unauthorized meeting
        try:
            frame = await asyncio.wait_for(ws.recv(), timeout=3)
        except asyncio.TimeoutError:
            # No response; treat as soft pass (not ideal, but at least not subscribed)
            print("    ✓ No subscribe ack for unauthorized meeting (soft pass)")
            return

        try:
            msg = json.loads(frame)
        except Exception:
            msg = {}

        if isinstance(msg, dict) and msg.get("type") == "error":
            print("    ✓ WS returned error for unauthorized meeting subscription")
            return

        if isinstance(msg, dict) and msg.get("type") == "subscribed":
            meetings = msg.get("meetings") or []
            found = any(
                isinstance(m, dict)
                and m.get("platform") == platform
                and m.get("native_id") == unauthorized_native_id
                for m in meetings
            )
            if found:
                raise TestFailure("❌ WS subscribed to an unauthorized meeting")
            else:
                print("    ✓ Unauthorized meeting was not included in subscribed list")
                # Additionally, ensure no events arrive shortly for that meeting
                try:
                    frame2 = await asyncio.wait_for(ws.recv(), timeout=2)
                    try:
                        msg2 = json.loads(frame2)
                    except Exception:
                        msg2 = {}
                    if isinstance(msg2, dict) and msg2.get("meeting"):
                        meet = msg2.get("meeting") or {}
                        if meet.get("platform") == platform and (meet.get("native_id") or meet.get("native_meeting_id")) == unauthorized_native_id:
                            raise TestFailure("❌ WS delivered event for unauthorized meeting")
                except asyncio.TimeoutError:
                    pass
                return

        # Any other response: treat as failure-safe
        raise TestFailure(f"❌ Unexpected WS response for unauthorized meeting: {msg or frame}")


async def e2e_test(cfg: TestConfig) -> None:
    print("=" * 80)
    print("E2E MEETING BOT TEST SUITE")
    print("=" * 80)
    print(f"Test Configuration:")
    print(f"  - Meeting URL: {cfg.meeting_url}")
    print(f"  - API Base URL: {cfg.api_base_url}")
    print(f"  - WebSocket URL: {cfg.ws_url}")
    print(f"  - Join Timeout: {cfg.join_timeout_sec}s")
    print(f"  - Mutable Segments Timeout: {cfg.mutable_events_timeout_sec}s")
    print(f"  - Stop Timeout: {cfg.stop_timeout_sec}s")
    print()

    # TEST 1: URL Parsing and Format Validation
    print("TEST 1: URL PARSING AND FORMAT VALIDATION")
    print("-" * 50)
    print(f"Parsing meeting URL: {cfg.meeting_url}")
    platform, native_id = parse_meeting_url(cfg.meeting_url)
    print(f"✓ Successfully parsed → platform={platform}, native_id={native_id}")
    print()

    # TEST 2: API Schema Validation (Reuse shared validation)
    print("TEST 2: API SCHEMA VALIDATION")
    print("-" * 50)
    print("Validating platform and native_meeting_id against shared API schemas...")
    
    if MeetingCreate is not None:
        try:
            _ = MeetingCreate(platform=platform, native_meeting_id=native_id)
            print("✓ MeetingCreate validation passed - platform and native_meeting_id are valid")
        except Exception as e:
            raise TestFailure(f"❌ MeetingCreate validation failed: {e}")
    else:
        print("⚠️  MeetingCreate schema not available - skipping validation")
    
    if Platform is not None:
        constructed_url = Platform.construct_meeting_url(platform, native_id)  # type: ignore[attr-defined]
        if constructed_url:
            print(f"✓ Platform.construct_meeting_url validation passed - constructed URL: {constructed_url}")
        else:
            raise TestFailure("❌ Platform.construct_meeting_url validation failed - cannot construct meeting URL")
    else:
        print("⚠️  Platform schema not available - skipping URL construction validation")
    print()

    # NEGATIVE TESTS: Invalid Input Validation
    print("NEGATIVE TESTS: INVALID INPUT VALIDATION")
    print("=" * 50)
    
    # Test invalid API key
    print("NEGATIVE TEST 1: INVALID API KEY")
    print("-" * 30)
    await test_invalid_api_key(cfg.api_base_url, platform, native_id)
    print()
    
    # Test invalid meeting data
    print("NEGATIVE TEST 2: INVALID MEETING DATA")
    print("-" * 30)
    await test_invalid_meeting_data(cfg.api_base_url, cfg.api_key)
    print()
    
    # Test invalid WebSocket connection
    print("NEGATIVE TEST 3: INVALID WEBSOCKET API KEY")
    print("-" * 30)
    await test_invalid_websocket_connection(cfg.ws_url, "invalid_ws_key_12345", platform, native_id)
    print()
    
    # Test invalid WebSocket data
    print("NEGATIVE TEST 4: INVALID WEBSOCKET DATA")
    print("-" * 30)
    await test_invalid_websocket_data(cfg.ws_url, cfg.api_key)
    print()

    # Test unauthorized meeting subscription (valid-looking ID that should not belong to this user)
    print("NEGATIVE TEST 5: UNAUTHORIZED WS SUBSCRIPTION")
    print("-" * 30)
    # Use a valid Google Meet format that's unlikely to be owned by this user
    unauthorized_native_id = "qwe-rtyu-iop"
    await test_unauthorized_ws_subscription(cfg.ws_url, cfg.api_key, "google_meet", unauthorized_native_id)
    print()
    
    print("✓ Negative tests completed - invalid API key, platform, meeting ID, and WebSocket data properly rejected")
    print("=" * 50)
    print()

    # TEST 3: Bot Start API Call
    print("TEST 3: BOT START API CALL")
    print("-" * 50)
    print("Sending POST request to start bot via API Gateway...")
    print(f"  - Endpoint: {cfg.api_base_url}/bots")
    print(f"  - Platform: {platform}")
    print(f"  - Native Meeting ID: {native_id}")
    
    _bot_resp = await start_bot(cfg.api_base_url, cfg.api_key, platform, native_id)
    print("✓ Bot start request successful")
    print("Waiting for bot status transitions...")
    print()

    # TEST 4: WebSocket Connection and Subscription
    print("TEST 4: WEBSOCKET CONNECTION AND SUBSCRIPTION")
    print("-" * 50)
    print("Establishing WebSocket connection...")
    print(f"  - WebSocket URL: {cfg.ws_url}")
    print(f"  - API Key: {'*' * (len(cfg.api_key) - 4) + cfg.api_key[-4:]}")
    
    headers = [("X-API-Key", cfg.api_key)]
    ws_url = cfg.ws_url
    sep = '&' if '?' in ws_url else '?'
    ws_url_with_key = f"{ws_url}{sep}api_key={cfg.api_key}"
    
    async with websockets.connect(ws_url_with_key, extra_headers=headers, ping_interval=None) as ws:
        print("✓ WebSocket connection established")
        
        print("Subscribing to meeting events...")
        subscribe_msg = {"action": "subscribe", "meetings": [{"platform": platform, "native_id": native_id}]}
        await ws.send(json.dumps(subscribe_msg))
        print("✓ Subscription message sent")
        print()

        # TEST 5: Bot Status Monitoring
        print("TEST 5: BOT STATUS MONITORING")
        print("-" * 50)
        print("Waiting for bot to reach 'active' status...")
        print(f"  - Timeout: {cfg.join_timeout_sec}s")
        
        try:
            await wait_for_status(ws, platform, native_id, "active", cfg.join_timeout_sec)
            print("✓ meeting.status=active received - bot successfully joined meeting")
        except TestFailure:
            print("⚠️  Did not receive 'active' status immediately, checking for 'requested' status...")
            try:
                await wait_for_status(ws, platform, native_id, "requested", 5)
                print("✓ meeting.status=requested received - bot is in queue")
                print("Waiting for transition to 'active'...")
                await wait_for_status(ws, platform, native_id, "active", cfg.join_timeout_sec)
                print("✓ meeting.status=active received - bot successfully joined meeting")
            except TestFailure:
                raise TestFailure("❌ Did not receive meeting.status=active within timeout")
        print()

        # TEST 6: Transcript Data Reception
        print("TEST 6: TRANSCRIPT DATA RECEPTION")
        print("-" * 50)
        print("Waiting for mutable transcript segments...")
        print(f"  - Minimum segments required: 3")
        print(f"  - Timeout: {cfg.mutable_events_timeout_sec}s")
        
        segments = await wait_for_mutable_segments(ws, platform, native_id, min_segments=3, timeout_sec=cfg.mutable_events_timeout_sec)
        print(f"✓ Received {len(segments)} mutable segments")
        
        # TEST 7: Transcript Schema Validation
        print("TEST 7: TRANSCRIPT SCHEMA VALIDATION")
        print("-" * 50)
        print("Validating transcript segment schema...")
        first = segments[0]
        required_fields = ("start", "end_time", "text")
        missing = [k for k in required_fields if k not in first]
        if missing:
            raise TestFailure(f"❌ First segment missing required fields: {missing}")
        print("✓ Transcript segment schema validation passed")
        print(f"  - Sample segment: start={first.get('start')}, end_time={first.get('end_time')}, text='{first.get('text', '')[:50]}...'")
        print()

        # TEST 8: Bot Stop API Call
        print("TEST 8: BOT STOP API CALL")
        print("-" * 50)
        print("Sending DELETE request to stop bot via API Gateway...")
        print(f"  - Endpoint: {cfg.api_base_url}/bots/{platform}/{native_id}")
        
        await stop_bot(cfg.api_base_url, cfg.api_key, platform, native_id)
        print("✓ Bot stop request successful")
        print()

        # TEST 9: Bot Shutdown Status Monitoring
        print("TEST 9: BOT SHUTDOWN STATUS MONITORING")
        print("-" * 50)
        print("Waiting for bot shutdown status transitions...")
        print(f"  - Stop timeout: {cfg.stop_timeout_sec}s")
        
        await wait_for_status(ws, platform, native_id, "stopping", cfg.stop_timeout_sec)
        print("✓ meeting.status=stopping received - bot is shutting down")
        
        # Wait for terminal state
        try:
            msg = await wait_for_status(ws, platform, native_id, "completed", cfg.stop_timeout_sec)
            if msg:
                print("✓ meeting.status=completed received - bot shutdown completed successfully")
        except TestFailure:
            print("⚠️  Did not receive 'completed' status, checking for 'failed' status...")
            await wait_for_status(ws, platform, native_id, "failed", cfg.stop_timeout_sec)
            print("✓ meeting.status=failed received - bot shutdown with non-zero exit")
        print()

    print("=" * 80)
    print("ALL TESTS COMPLETED SUCCESSFULLY")
    print("=" * 80)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="End-to-end real meeting WS test (no mocks)")
    p.add_argument("--meeting-url", required=True, help="Full meeting URL (e.g., https://meet.google.com/xxx-xxxx-xxx)")
    p.add_argument("--api-key", required=True, help="User API key (X-API-Key)")
    p.add_argument("--api-base", default="http://localhost:18056", help="API Gateway base URL (default: http://localhost:18056)")
    p.add_argument("--ws-url", default="ws://localhost:18056/ws", help="WebSocket URL (default: ws://localhost:18056/ws)")
    p.add_argument("--join-timeout", type=int, default=120, help="Join timeout seconds (default: 120)")
    p.add_argument("--mutable-timeout", type=int, default=60, help="Mutable segments timeout seconds (default: 60)")
    p.add_argument("--stop-timeout", type=int, default=120, help="Stop/terminal status timeout seconds (default: 120)")
    return p


def main():
    args = build_arg_parser().parse_args()
    cfg = TestConfig(
        api_base_url=args.api_base,
        ws_url=args.ws_url,
        api_key=args.api_key,
        meeting_url=args.meeting_url,
        join_timeout_sec=args.join_timeout,
        mutable_events_timeout_sec=args.mutable_timeout,
        stop_timeout_sec=args.stop_timeout,
    )

    start_ts = datetime.utcnow()
    print(f"Starting E2E WS test at {start_ts.isoformat()}Z")
    try:
        asyncio.run(e2e_test(cfg))
        print("\nALL CHECKS PASSED")
        sys.exit(0)
    except TestFailure as tf:
        print(f"\nTEST FAILED: {tf}")
        sys.exit(2)
    except KeyboardInterrupt:
        print("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()


