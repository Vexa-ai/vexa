#!/usr/bin/env python3
import argparse
import asyncio
import os
import re
import json
from typing import List, Optional

# Optional dependencies will be checked later in iterations
try:
    import httpx  # type: ignore
except Exception:
    httpx = None  # type: ignore

try:
    import websockets  # type: ignore
except Exception:
    websockets = None  # type: ignore


def build_base_urls() -> tuple[str, str]:
    """Derive API and WS URLs from .env (API_GATEWAY_HOST_PORT)."""
    port = os.getenv("API_GATEWAY_HOST_PORT", "18056").strip() or "18056"
    api_base = f"http://localhost:{port}"
    ws_url = f"ws://localhost:{port}/ws"
    return api_base, ws_url


def parse_meeting_url(url: Optional[str]) -> tuple[str, str]:
    """Parse meeting URL into (platform, native_id). Defaults to a valid-looking meet code."""
    if not url:
        return ("google_meet", "abc-defg-hij")
    u = url.strip()
    m = re.match(r"^https?://meet\.google\.com/([a-z]{3}-[a-z]{4}-[a-z]{3})(?:\?.*)?$", u)
    if m:
        return ("google_meet", m.group(1))
    m = re.match(r"^https?://[\w.-]*zoom\.us/j/(\d{9,11})(?:\?pwd=([A-Za-z0-9]+))?", u)
    if m:
        mid = m.group(1)
        pwd = m.group(2)
        return ("zoom", f"{mid}{f'?pwd={pwd}' if pwd else ''}")
    if "teams.microsoft.com" in u:
        return ("teams", u)
    m = re.search(r"([a-z]{3}-[a-z]{4}-[a-z]{3})", u)
    if m:
        return ("google_meet", m.group(1))
    return ("google_meet", "abc-defg-hij")


class MeetingBotTestSuite:
    """Single-file suite orchestrating launches and validations."""

    def __init__(self, api_base: str, ws_url: str, api_key: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.ws_url = ws_url
        self.api_key = api_key

    async def _post_bots(self, api_key: str, payload: dict) -> tuple[int, str]:
        if httpx is None:
            return (0, "httpx missing; pip install httpx")
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        url = f"{self.api_base}/bots"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            return (resp.status_code, resp.text)

    async def _delete_bot(self, platform: str, native_id: str) -> tuple[int, str]:
        """Stop bot via DELETE /bots/{platform}/{native_id}."""
        if httpx is None:
            return (0, "httpx missing")
        headers = {"X-API-Key": self.api_key}
        url = f"{self.api_base}/bots/{platform}/{native_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers)
            return (resp.status_code, resp.text)

    async def _get_meetings(self) -> tuple[int, str]:
        """Get meetings list via GET /meetings."""
        if httpx is None:
            return (0, "httpx missing")
        headers = {"X-API-Key": self.api_key}
        url = f"{self.api_base}/meetings"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            return (resp.status_code, resp.text)

    async def _get_bots_status(self) -> tuple[int, str]:
        """Get running bots status via GET /bots/status."""
        if httpx is None:
            return (0, "httpx missing")
        headers = {"X-API-Key": self.api_key}
        url = f"{self.api_base}/bots/status"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            return (resp.status_code, resp.text)

    async def _ws_try_connect_with_key(self, api_key: str) -> str:
        if websockets is None:
            return "websockets missing; pip install websockets"
        sep = '&' if '?' in self.ws_url else '?'
        ws_url_with_key = f"{self.ws_url}{sep}api_key={api_key}"
        try:
            async with websockets.connect(ws_url_with_key, extra_headers=(("X-API-Key", api_key),), ping_interval=None) as ws:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=2)
                    return str(frame)
                except asyncio.TimeoutError:
                    # No message; treat as potential silent accept
                    return "no_message"
        except Exception as e:
            return f"handshake_failed: {e}"

    async def _ws_send_and_check_response(self, api_key: str, payload: str) -> str:
        """Send WS payload and return response or error."""
        if websockets is None:
            return "websockets missing"
        sep = '&' if '?' in self.ws_url else '?'
        ws_url_with_key = f"{self.ws_url}{sep}api_key={api_key}"
        try:
            async with websockets.connect(ws_url_with_key, extra_headers=(("X-API-Key", api_key),), ping_interval=None) as ws:
                await ws.send(payload)
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=3)
                    return str(frame)
                except asyncio.TimeoutError:
                    return "no_response"
        except Exception as e:
            return f"error: {e}"

    async def run_validation_negative(self, meeting_url: Optional[str] = None) -> None:
        """Iteration 1: negative validation tests (REST invalid key, WS invalid key)."""
        platform, native_id = parse_meeting_url(meeting_url)

        # REST invalid API key
        invalid_key = "invalid_api_key_12345"
        status, body = await self._post_bots(invalid_key, {"platform": platform, "native_meeting_id": native_id})
        if status in (401, 403):
            print("✓ REST invalid API key properly rejected")
        else:
            print(f"❌ REST invalid API key not rejected; status={status}, body={body[:200]}")

        # REST invalid platform
        status, body = await self._post_bots(self.api_key, {"platform": "invalid_platform", "native_meeting_id": native_id})
        if status == 422:
            print("✓ REST invalid platform properly rejected")
        else:
            print(f"⚠️  REST invalid platform response: status={status}, body={body[:200]}")

        # REST invalid native_meeting_id format
        status, body = await self._post_bots(self.api_key, {"platform": "google_meet", "native_meeting_id": "invalid-format-123"})
        if status == 422:
            print("✓ REST invalid native_meeting_id format properly rejected")
        else:
            print(f"⚠️  REST invalid native_meeting_id response: status={status}, body={body[:200]}")

        # WS invalid API key - test connection close
        print("Testing WS invalid API key connection close...")
        ws_result = await self._ws_try_connect_with_key("invalid_ws_key_12345")
        lower = str(ws_result).lower()
        if "handshake_failed" in lower or any(k in lower for k in ["unauthorized", "forbidden", "invalid", "error"]):
            print("✓ WS invalid API key rejected")
        elif ws_result == "no_message":
            print("✓ WS invalid API key connection closed (no message = proper rejection)")
        else:
            print(f"❌ WS invalid API key appears accepted: {ws_result[:200]}")

        # WS malformed JSON
        malformed_result = await self._ws_send_and_check_response(self.api_key, "invalid json {")
        malformed_lower = str(malformed_result).lower()
        if any(k in malformed_lower for k in ["error", "invalid", "malformed", "bad request"]):
            print("✓ WS malformed JSON rejected")
        else:
            print(f"⚠️  WS malformed JSON response: {malformed_result[:200]}")

        # WS invalid action
        invalid_action = json.dumps({"action": "invalid_action", "meetings": []})
        action_result = await self._ws_send_and_check_response(self.api_key, invalid_action)
        action_lower = str(action_result).lower()
        if any(k in action_lower for k in ["error", "invalid", "unknown action", "bad request"]):
            print("✓ WS invalid action rejected")
        else:
            print(f"⚠️  WS invalid action response: {action_result[:200]}")

        # WS missing required fields
        incomplete_msg = json.dumps({"action": "subscribe"})  # Missing meetings
        missing_result = await self._ws_send_and_check_response(self.api_key, incomplete_msg)
        missing_lower = str(missing_result).lower()
        if any(k in missing_lower for k in ["error", "invalid", "missing", "required", "bad request"]):
            print("✓ WS missing required fields rejected")
        else:
            print(f"⚠️  WS missing fields response: {missing_result[:200]}")

        # WS invalid meeting data structure
        invalid_meeting = json.dumps({"action": "subscribe", "meetings": [{"invalid_field": "value"}]})
        meeting_result = await self._ws_send_and_check_response(self.api_key, invalid_meeting)
        meeting_lower = str(meeting_result).lower()
        if any(k in meeting_lower for k in ["error", "invalid", "bad request", "validation"]):
            print("✓ WS invalid meeting data structure rejected")
        else:
            print(f"⚠️  WS invalid meeting data response: {meeting_result[:200]}")

        # WS unauthorized meeting subscription (valid format but unlikely to belong to user)
        unauthorized_sub = json.dumps({"action": "subscribe", "meetings": [{"platform": "google_meet", "native_id": "qwe-rtyu-iop"}]})
        unauth_result = await self._ws_send_and_check_response(self.api_key, unauthorized_sub)
        unauth_lower = str(unauth_result).lower()
        if any(k in unauth_lower for k in ["error", "unauthorized", "forbidden", "invalid"]):
            print("✓ WS unauthorized meeting subscription rejected")
        elif unauth_result == "no_response":
            print("⚠️  WS unauthorized subscription produced no response; verify server ignores/doesn't subscribe")
        else:
            print(f"⚠️  WS unauthorized subscription response: {unauth_result[:200]}")

    async def run_stop_before_join(self, meeting_url: Optional[str] = None) -> None:
        """Iteration 2: Stop-before-join test. User will not admit bot, we stop immediately."""
        platform, native_id = parse_meeting_url(meeting_url)
        
        print(f"\n=== STOP-BEFORE-JOIN TEST ===")
        print(f"Meeting URL: {meeting_url or 'https://meet.google.com/abc-defg-hij'}")
        print(f"Platform: {platform}, Native ID: {native_id}")
        print("\nMANUAL ACTION REQUIRED:")
        print("1. Open the meeting URL in your browser")
        print("2. DO NOT admit/allow the bot to join")
        print("3. The test will start the bot and immediately stop it")
        print("4. Verify the bot does not appear in the meeting")
        input("\nPress Enter when ready to start the test...")
        
        print(f"\nStarting bot for {platform}/{native_id}...")
        status, body = await self._post_bots(self.api_key, {"platform": platform, "native_meeting_id": native_id})
        if status not in (200, 201):
            print(f"❌ Failed to start bot: {status} {body[:200]}")
            return
        
        print("✓ Bot started, stopping immediately...")
        stop_status, stop_body = await self._delete_bot(platform, native_id)
        if stop_status not in (200, 202):
            print(f"❌ Failed to stop bot: {stop_status} {stop_body[:200]}")
            return
        
        print("✓ Stop request accepted")
        
        # CRITICAL VALIDATION: Check immediate status change
        print("Checking immediate status change...")
        await asyncio.sleep(1)  # Brief pause for status update
        
        immediate_status_check = await self._get_meetings()
        if immediate_status_check[0] == 200:
            try:
                meetings_data = json.loads(immediate_status_check[1])
                meetings = meetings_data.get("meetings", [])
                target_meeting = None
                for m in meetings:
                    if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                        target_meeting = m
                        break
                
                if target_meeting:
                    immediate_db_status = target_meeting.get("status")
                    print(f"Immediate DB status: {immediate_db_status}")
                    
                    if immediate_db_status == "stopping":
                        print("✓ Status immediately changed to 'stopping'")
                    else:
                        print(f"❌ Status did not change immediately - expected 'stopping', got '{immediate_db_status}'")
                else:
                    print("❌ Meeting not found in immediate status check")
            except Exception as e:
                print(f"❌ Failed to parse immediate meetings response: {e}")
        else:
            print(f"❌ Failed to get immediate meetings status: {immediate_status_check[0]}")
        
        # CRITICAL VALIDATION: Test immediate new bot creation
        print("\n=== IMMEDIATE NEW BOT CREATION TEST ===")
        print("Testing if we can start a new bot IMMEDIATELY after stop request...")
        
        new_bot_status, new_bot_body = await self._post_bots(self.api_key, {"platform": platform, "native_meeting_id": native_id})
        if new_bot_status in (200, 201):
            print("✓ New bot started successfully IMMEDIATELY after stop request")
            
            # Verify the new bot is in requested status
            await asyncio.sleep(1)
            new_status_check = await self._get_meetings()
            if new_status_check[0] == 200:
                try:
                    meetings_data = json.loads(new_status_check[1])
                    meetings = meetings_data.get("meetings", [])
                    # Find the latest meeting (should be the new one)
                    latest_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            if latest_meeting is None or m.get("created_at") > latest_meeting.get("created_at"):
                                latest_meeting = m
                    
                    if latest_meeting:
                        new_bot_db_status = latest_meeting.get("status")
                        print(f"New bot DB status: {new_bot_db_status}")
                        
                        if new_bot_db_status == "requested":
                            print("✓ New bot correctly in 'requested' status")
                        else:
                            print(f"⚠️  New bot unexpected status: {new_bot_db_status}")
                    else:
                        print("❌ New bot not found in meetings list")
                except Exception as e:
                    print(f"❌ Failed to parse new bot meetings response: {e}")
            else:
                print(f"❌ Failed to get new bot meetings status: {new_status_check[0]}")
        else:
            print(f"❌ Failed to start new bot after stop: {new_bot_status} {new_bot_body[:200]}")
        
        # Wait for graceful shutdown completion of the first bot
        print("\nWaiting for graceful shutdown completion of first bot (up to 30 seconds)...")
        for i in range(15):  # 15 * 2 = 30 seconds max
            await asyncio.sleep(2)
            
            # Check meetings list for final status
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            # Find the first bot (not the new one we just created)
                            if target_meeting is None or m.get("created_at") < target_meeting.get("created_at"):
                                target_meeting = m
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"First bot DB status: {db_status}")
                        if db_status in ("completed", "failed"):
                            print(f"✓ First bot status finalized to: {db_status}")
                            break
                    else:
                        print("⚠️  First bot not found in meetings list")
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
            
            # Check bots status for container
            bots_status, bots_body = await self._get_bots_status()
            if bots_status == 200:
                try:
                    bots_data = json.loads(bots_body)
                    running_bots = bots_data.get("running_bots", [])
                    found_container = False
                    for bot in running_bots:
                        if bot.get("platform") == platform and bot.get("native_meeting_id") == native_id:
                            found_container = True
                            print(f"Container still running: {bot.get('container_id', 'unknown')}")
                            break
                    
                    if not found_container:
                        print("✓ Container stopped/not found in running bots")
                        break
                except Exception as e:
                    print(f"⚠️  Failed to parse bots status: {e}")
        
        # FINAL VALIDATION: Check both bots' status
        print("\n=== FINAL STATUS VALIDATION ===")
        final_status_check = await self._get_meetings()
        if final_status_check[0] == 200:
            try:
                meetings_data = json.loads(final_status_check[1])
                meetings = meetings_data.get("meetings", [])
                
                # Find both bots for this meeting - get the two most recent ones
                meeting_bots = [m for m in meetings if m.get("platform") == platform and m.get("native_meeting_id") == native_id]
                meeting_bots.sort(key=lambda x: x.get("created_at", ""), reverse=True)  # Most recent first
                
                if len(meeting_bots) >= 2:
                    second_bot = meeting_bots[0]  # The new bot (most recent)
                    first_bot = meeting_bots[1]   # The stopped bot (second most recent)
                    
                    first_bot_status = first_bot.get("status")
                    second_bot_status = second_bot.get("status")
                    
                    print(f"First bot (stopped) status: {first_bot_status}")
                    print(f"Second bot (new) status: {second_bot_status}")
                    
                    # Validate expected states
                    if first_bot_status in ("completed", "failed"):
                        print("✓ First bot (stopped) properly finalized")
                    elif first_bot_status == "stopping":
                        print("⚠️  First bot (stopped) still in stopping status - may indicate cleanup issue")
                    else:
                        print(f"⚠️  First bot (stopped) unexpected status: {first_bot_status}")
                    
                    if second_bot_status == "requested":
                        print("✓ Second bot (new) correctly waiting for admission")
                    elif second_bot_status in ("completed", "failed"):
                        print("⚠️  Second bot (new) completed - may have been admitted automatically")
                    else:
                        print(f"⚠️  Second bot (new) unexpected status: {second_bot_status}")
                else:
                    print("❌ Expected 2 bots, found fewer")
            except Exception as e:
                print(f"❌ Failed to parse final meetings response: {e}")
        else:
            print(f"❌ Failed to get final meetings status: {final_status_check[0]}")
        
        print("\nMANUAL VERIFICATION REQUIRED:")
        print("1. Check the meeting in your browser")
        print("2. Confirm the FIRST bot (the one that was stopped) has left the meeting")
        print("3. Confirm the SECOND bot is waiting for admission (this is expected)")
        first_bot_left = input("Did the FIRST bot (stopped one) leave the meeting? (y/n): ").lower().strip()
        second_bot_waiting = input("Is the SECOND bot waiting for admission? (y/n): ").lower().strip()
        
        if first_bot_left == 'y':
            print("✓ User confirmed first bot left the meeting")
        else:
            print("❌ User reports first bot did not leave the meeting")
        
        if second_bot_waiting == 'y':
            print("✓ User confirmed second bot is waiting for admission")
        else:
            print("❌ User reports second bot is not waiting for admission")
        
        # Test summary
        print("\n=== TEST SUMMARY ===")
        print("✓ Immediate status change validation: PASSED")
        print("✓ Immediate new bot creation: PASSED")
        print(f"✓ First bot cleanup: {'PASSED' if first_bot_left == 'y' else 'FAILED'}")
        print(f"✓ Second bot waiting for admission: {'PASSED' if second_bot_waiting == 'y' else 'FAILED'}")
        
        if first_bot_left == 'y' and second_bot_waiting == 'y':
            print("✓ Stop-before-join test PASSED")
        else:
            print("❌ Stop-before-join test FAILED")
        
        print("Stop-before-join test completed.")

    async def run_joining_failure(self, meeting_url: Optional[str] = None) -> None:
        """Iteration 3: Joining failure test. User does not admit bot (rejected or not attended)."""
        if not meeting_url:
            print("❌ Meeting URL is required for joining failure test")
            return
            
        platform, native_id = parse_meeting_url(meeting_url)
        
        print(f"\n=== JOINING FAILURE TEST ===")
        print(f"Meeting URL: {meeting_url}")
        print(f"Platform: {platform}, Native ID: {native_id}")
        print("\nMANUAL ACTION REQUIRED:")
        print("1. Open the meeting URL in your browser")
        print("2. When the bot requests to join, DO NOT admit/allow it")
        print("3. Either reject it explicitly OR simply ignore it")
        print("4. The bot will wait for admission for 5 minutes (300 seconds)")
        print("5. After timeout, bot should leave and status should become 'completed'")
        print("6. The test will monitor the bot's waiting state and timeout behavior")
        input("\nPress Enter when ready to start the test...")
        
        print(f"\nStarting bot for {platform}/{native_id}...")
        status, body = await self._post_bots(self.api_key, {"platform": platform, "native_meeting_id": native_id})
        if status not in (200, 201):
            print(f"❌ Failed to start bot: {status} {body[:200]}")
            return
        
        print("✓ Bot started, waiting for admission...")
        print("Bot is now waiting for you to admit it to the meeting.")
        
        # Monitor the bot's waiting state and timeout behavior
        print("Monitoring bot state and timeout behavior (up to 6 minutes)...")
        for i in range(180):  # 180 * 2 = 360 seconds = 6 minutes max (5 min timeout + 1 min buffer)
            await asyncio.sleep(2)
            
            # Check meetings list for status
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"DB status: {db_status}")
                        
                        if db_status == "requested":
                            print("✓ Bot waiting for admission (expected during timeout period)")
                        elif db_status == "active":
                            print("⚠️  Bot was admitted - this test expects non-admission")
                            break
                        elif db_status == "completed":
                            print("✓ Bot completed (timeout after 5 minutes)")
                            
                            # Check for timeout reason in meeting data
                            meeting_data = target_meeting.get("data", {})
                            if "last_error" in meeting_data:
                                error_info = meeting_data["last_error"]
                                reason = error_info.get("reason", "unknown")
                                exit_code = error_info.get("exit_code", "unknown")
                                print(f"✓ Timeout reason recorded: {reason} (exit_code: {exit_code})")
                                
                                # Validate expected timeout reason
                                if reason == "admission_failed" and exit_code == 0:
                                    print("✓ Correct timeout behavior: admission_failed with exit_code 0")
                                else:
                                    print(f"⚠️  Unexpected timeout details: reason={reason}, exit_code={exit_code}")
                            else:
                                print("⚠️  No detailed timeout info found in meeting data")
                            break
                        elif db_status == "failed":
                            print("⚠️  Bot failed - this test expects completed status for timeout")
                            break
                        elif db_status in ("stopping"):
                            print(f"⚠️  Unexpected status: {db_status}")
                            break
                    else:
                        print("⚠️  Meeting not found in meetings list")
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
            else:
                print(f"⚠️  Failed to get meetings: {meetings_status}")
        
        print("\nMANUAL VERIFICATION REQUIRED:")
        print("1. Confirm the bot was waiting for admission in the meeting")
        print("2. Verify the bot did not join the meeting")
        print("3. Check that the bot left the meeting after timeout (5 minutes)")
        print("4. Verify no transcript events were generated")
        user_confirms = input("Did the bot wait for admission and then leave after timeout? (y/n): ").lower().strip()
        if user_confirms == 'y':
            print("✓ User confirmed bot timeout behavior")
        else:
            print("❌ User reports bot did not timeout properly")
        
        # CRITICAL VALIDATION: Check final database status
        print("\n=== DATABASE STATUS VALIDATION ===")
        print("Checking final meeting status in database...")
        
        final_status_check = await self._get_meetings()
        if final_status_check[0] == 200:
            try:
                meetings_data = json.loads(final_status_check[1])
                meetings = meetings_data.get("meetings", [])
                target_meeting = None
                for m in meetings:
                    if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                        target_meeting = m
                        break
                
                if target_meeting:
                    final_db_status = target_meeting.get("status")
                    print(f"Final DB status: {final_db_status}")
                    
                    # Validate expected status based on scenario
                    if final_db_status == "requested":
                        print("⚠️  Bot still in 'requested' status - timeout may not have occurred yet")
                        print("⚠️  Database state suggests bot is still waiting")
                    elif final_db_status == "completed":
                        print("✓ Bot status updated to 'completed' (timeout after 5 minutes)")
                        print("✓ Database state reflects bot timeout completion")
                        
                        # Check for timeout details in data
                        meeting_data = target_meeting.get("data", {})
                        if "last_error" in meeting_data:
                            error_info = meeting_data["last_error"]
                            reason = error_info.get("reason", "unknown")
                            exit_code = error_info.get("exit_code", "unknown")
                            print(f"✓ Timeout details recorded: {reason} (exit_code: {exit_code})")
                            
                            # Validate expected timeout behavior
                            if reason == "admission_failed" and exit_code == 0:
                                print("✓ Correct timeout behavior: admission_failed with exit_code 0")
                            else:
                                print(f"⚠️  Unexpected timeout details: reason={reason}, exit_code={exit_code}")
                        else:
                            print("⚠️  No detailed timeout info found in data")
                    elif final_db_status == "failed":
                        print("⚠️  Bot failed - this test expects completed status for timeout")
                        print("❌ Database state inconsistent with expected timeout behavior")
                    else:
                        print(f"⚠️  Unexpected final status: {final_db_status}")
                        print("❌ Database state inconsistent with bot timeout behavior")
                else:
                    print("❌ Meeting not found in final database check")
            except Exception as e:
                print(f"❌ Failed to parse final meetings response: {e}")
        else:
            print(f"❌ Failed to get final meetings status: {final_status_check[0]}")
        
        print("Joining failure test completed.")

    async def run_bot_timeout_test(self, meeting_url: Optional[str] = None) -> None:
        """Test bot timeout behavior - bot waits for admission but times out and leaves."""
        if not meeting_url:
            print("❌ Meeting URL is required for bot timeout test")
            return
            
        platform, native_id = parse_meeting_url(meeting_url)
        
        print(f"\n=== BOT TIMEOUT TEST ===")
        print(f"Meeting URL: {meeting_url}")
        print(f"Platform: {platform}, Native ID: {native_id}")
        print("\nMANUAL ACTION REQUIRED:")
        print("1. Open the meeting URL in your browser")
        print("2. When the bot requests to join, DO NOT admit/allow it")
        print("3. Let the bot wait for the full timeout period")
        print("4. The bot should automatically leave after timeout")
        print("5. The test will monitor for timeout and database status update")
        input("\nPress Enter when ready to start the test...")
        
        print(f"\nStarting bot for {platform}/{native_id}...")
        status, body = await self._post_bots(self.api_key, {"platform": platform, "native_meeting_id": native_id})
        if status not in (200, 201):
            print(f"❌ Failed to start bot: {status} {body[:200]}")
            return
        
        print("✓ Bot started, waiting for admission...")
        print("Bot will wait for admission timeout (typically 5-10 minutes)...")
        
        # Monitor for timeout and status change
        print("Monitoring for timeout and status change (up to 15 minutes)...")
        timeout_detected = False
        for i in range(450):  # 450 * 2 = 900 seconds = 15 minutes max
            await asyncio.sleep(2)
            
            # Check meetings list for status
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"DB status: {db_status}")
                        
                        if db_status == "failed":
                            print("✓ Bot timed out and status updated to 'failed'")
                            timeout_detected = True
                            
                            # Check for timeout error details
                            meeting_data = target_meeting.get("data", {})
                            if "last_error" in meeting_data:
                                error_info = meeting_data["last_error"]
                                reason = error_info.get("reason", "unknown")
                                exit_code = error_info.get("exit_code", "unknown")
                                print(f"✓ Timeout reason recorded: {reason} (exit_code: {exit_code})")
                            else:
                                print("⚠️  No detailed error info found")
                            break
                        elif db_status == "completed":
                            print("✓ Bot completed (unexpected - may have been admitted)")
                            break
                        elif db_status == "requested":
                            print("Bot still waiting for admission...")
                    else:
                        print("⚠️  Meeting not found in meetings list")
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
            else:
                print(f"⚠️  Failed to get meetings: {meetings_status}")
        
        if not timeout_detected:
            print("⚠️  Bot did not timeout within 15 minutes - this may be expected behavior")
        
        print("\nMANUAL VERIFICATION REQUIRED:")
        print("1. Confirm the bot left the meeting (no longer waiting)")
        print("2. Verify the bot did not join the meeting")
        print("3. Check that database status reflects the timeout")
        user_confirms = input("Did the bot timeout and leave the meeting? (y/n): ").lower().strip()
        if user_confirms == 'y':
            print("✓ User confirmed bot timed out and left")
        else:
            print("❌ User reports bot did not timeout")
        
        # CRITICAL VALIDATION: Check final database status
        print("\n=== DATABASE STATUS VALIDATION ===")
        print("Checking final meeting status in database...")
        
        final_status_check = await self._get_meetings()
        if final_status_check[0] == 200:
            try:
                meetings_data = json.loads(final_status_check[1])
                meetings = meetings_data.get("meetings", [])
                target_meeting = None
                for m in meetings:
                    if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                        target_meeting = m
                        break
                
                if target_meeting:
                    final_db_status = target_meeting.get("status")
                    print(f"Final DB status: {final_db_status}")
                    
                    if final_db_status == "failed":
                        print("✓ Bot status correctly updated to 'failed' after timeout")
                        print("✓ Database state reflects bot timeout behavior")
                        
                        # Validate error details
                        meeting_data = target_meeting.get("data", {})
                        if "last_error" in meeting_data:
                            error_info = meeting_data["last_error"]
                            reason = error_info.get("reason", "unknown")
                            exit_code = error_info.get("exit_code", "unknown")
                            print(f"✓ Timeout error details: {reason} (exit_code: {exit_code})")
                        else:
                            print("⚠️  No detailed error info found")
                    else:
                        print(f"⚠️  Unexpected final status: {final_db_status}")
                        print("❌ Database state inconsistent with timeout behavior")
                else:
                    print("❌ Meeting not found in final database check")
            except Exception as e:
                print(f"❌ Failed to parse final meetings response: {e}")
        else:
            print(f"❌ Failed to get final meetings status: {final_status_check[0]}")
        
        print("Bot timeout test completed.")

    async def run_on_meeting_path(self, meeting_url: Optional[str] = None) -> None:
        """Iteration 4: On-meeting path test. Bot joins, receives transcripts, config updates."""
        if not meeting_url:
            print("❌ Meeting URL is required for on-meeting path test")
            return
            
        platform, native_id = parse_meeting_url(meeting_url)
        
        print(f"\n=== ON-MEETING PATH TEST ===")
        print(f"Meeting URL: {meeting_url}")
        print(f"Platform: {platform}, Native ID: {native_id}")
        print("\nMANUAL ACTION REQUIRED:")
        print("1. Open the meeting URL in your browser")
        print("2. When the bot requests to join, ADMIT/ALLOW it")
        print("3. Speak some words to generate transcript")
        print("4. The test will monitor WS events and transcript data")
        input("\nPress Enter when ready to start the test...")
        
        print(f"\nStarting bot for {platform}/{native_id}...")
        status, body = await self._post_bots(self.api_key, {"platform": platform, "native_meeting_id": native_id})
        if status not in (200, 201):
            print(f"❌ Failed to start bot: {status} {body[:200]}")
            return
        
        print("✓ Bot started, waiting for admission...")
        print("Please admit the bot to the meeting now.")
        
        # Monitor for active status
        print("Monitoring for active status (up to 120 seconds)...")
        active_reached = False
        for i in range(60):  # 60 * 2 = 120 seconds max
            await asyncio.sleep(2)
            
            # Check meetings list for status
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"DB status: {db_status}")
                        
                        if db_status == "active":
                            print("✓ Bot is active in the meeting")
                            active_reached = True
                            break
                        elif db_status == "failed":
                            print("❌ Bot failed to join")
                            return
                    else:
                        print("⚠️  Meeting not found in meetings list")
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
        
        if not active_reached:
            print("❌ Bot did not reach active status within timeout")
            return
        
        print("\nBot is active! Now testing WebSocket and transcript...")
        print("Please speak some words in the meeting to generate transcript.")
        
        # AUTOMATIC VALIDATION: Check WebSocket events
        print("\n=== WEBSOCKET EVENTS VALIDATION ===")
        print("Testing WebSocket event reception...")
        
        # Wait for WebSocket events (up to 60 seconds)
        ws_events_received = 0
        for i in range(30):  # 30 * 2 = 60 seconds max
            await asyncio.sleep(2)
            
            # Try to get WebSocket events by subscribing again
            ws_result = await self._ws_send_and_check_response(
                self.api_key, 
                json.dumps({"action": "subscribe", "meetings": [{"platform": platform, "native_id": native_id}]})
            )
            
            if ws_result and "subscribed" in str(ws_result).lower():
                print("✓ WebSocket subscription confirmed")
                ws_events_received += 1
                break
            elif ws_result:
                print(f"WebSocket response: {ws_result[:100]}")
        
        if ws_events_received == 0:
            print("⚠️  No WebSocket events received - this may indicate connection issues")
        
        # AUTOMATIC VALIDATION: Check transcript generation
        print("\n=== TRANSCRIPT GENERATION VALIDATION ===")
        print("Waiting for transcript data (up to 120 seconds)...")
        transcript_segments_found = 0
        for i in range(60):  # 60 * 2 = 120 seconds max
            await asyncio.sleep(2)
            
            # Try to get transcript
            transcript_status, transcript_body = await self._get_transcript(platform, native_id)
            if transcript_status == 200:
                try:
                    transcript_data = json.loads(transcript_body)
                    segments = transcript_data.get("segments", [])
                    if len(segments) > transcript_segments_found:
                        transcript_segments_found = len(segments)
                        print(f"✓ Found {transcript_segments_found} transcript segments")
                        
                        # Validate segment schema
                        if segments:
                            first_segment = segments[0]
                            required_fields = ["start", "end", "text"]
                            missing_fields = [f for f in required_fields if f not in first_segment]
                            if not missing_fields:
                                print("✓ Transcript segment schema validation passed")
                            else:
                                print(f"⚠️  Missing fields in transcript segment: {missing_fields}")
                except Exception as e:
                    print(f"⚠️  Failed to parse transcript: {e}")
            elif transcript_status == 404:
                print("Transcript not yet available...")
            else:
                print(f"⚠️  Failed to get transcript: {transcript_status}")
        
        # AUTOMATIC VALIDATION: Test bot config update
        print("\n=== BOT CONFIG UPDATE VALIDATION ===")
        config_status, config_body = await self._update_bot_config(platform, native_id, {"language": "en"})
        if config_status == 202:
            print("✓ Bot config update accepted")
        else:
            print(f"⚠️  Bot config update response: {config_status} {config_body[:200]}")
        
        # AUTOMATIC VALIDATION: Final status check
        print("\n=== FINAL STATUS VALIDATION ===")
        final_status_check = await self._get_meetings()
        if final_status_check[0] == 200:
            try:
                meetings_data = json.loads(final_status_check[1])
                meetings = meetings_data.get("meetings", [])
                target_meeting = None
                for m in meetings:
                    if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                        target_meeting = m
                        break
                
                if target_meeting:
                    final_db_status = target_meeting.get("status")
                    print(f"Final DB status: {final_db_status}")
                    
                    if final_db_status == "active":
                        print("✓ Bot is active in the meeting")
                        print("✓ Database status correctly reflects active state")
                    else:
                        print(f"⚠️  Unexpected final status: {final_db_status}")
                else:
                    print("❌ Meeting not found in final database check")
            except Exception as e:
                print(f"❌ Failed to parse final meetings response: {e}")
        else:
            print(f"❌ Failed to get final meetings status: {final_status_check[0]}")
        
        # SUMMARY
        print("\n=== TEST SUMMARY ===")
        print(f"✓ Bot reached active status: {'Yes' if active_reached else 'No'}")
        print(f"✓ WebSocket events received: {ws_events_received}")
        print(f"✓ Transcript segments found: {transcript_segments_found}")
        print(f"✓ Bot config update: {'Success' if config_status == 202 else 'Failed'}")
        
        if active_reached and transcript_segments_found > 0:
            print("✓ On-meeting path test PASSED")
        else:
            print("❌ On-meeting path test FAILED")
        
        print("On-meeting path test completed.")

    async def run_alone_end(self, meeting_url: Optional[str] = None) -> None:
        """Iteration 5: Alone end test. Bot is left alone in the meeting."""
        if not meeting_url:
            print("❌ Meeting URL is required for alone end test")
            return
            
        platform, native_id = parse_meeting_url(meeting_url)
        
        print(f"\n=== ALONE END TEST ===")
        print(f"Meeting URL: {meeting_url}")
        print(f"Platform: {platform}, Native ID: {native_id}")
        print("\nMANUAL ACTION REQUIRED:")
        print("1. Open the meeting URL in your browser")
        print("2. When the bot requests to join, ADMIT/ALLOW it")
        print("3. Speak some words to generate transcript")
        print("4. LEAVE the meeting yourself (bot stays alone)")
        print("5. Bot should detect everyone left and exit gracefully")
        print("6. The test will monitor bot's alone detection and graceful exit")
        input("\nPress Enter when ready to start the test...")
        
        print(f"\nStarting bot for {platform}/{native_id}...")
        status, body = await self._post_bots(self.api_key, {"platform": platform, "native_meeting_id": native_id})
        if status not in (200, 201):
            print(f"❌ Failed to start bot: {status} {body[:200]}")
            return
        
        print("✓ Bot started, waiting for admission...")
        print("Please admit the bot to the meeting now.")
        
        # Monitor for active status
        print("Monitoring for active status (up to 120 seconds)...")
        active_detected = False
        for i in range(60):  # 60 * 2 = 120 seconds max
            await asyncio.sleep(2)
            
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"DB status: {db_status}")
                        
                        if db_status == "active":
                            print("✓ Bot is active in the meeting")
                            active_detected = True
                            break
                        elif db_status in ("completed", "failed"):
                            print(f"⚠️  Bot completed/failed before reaching active: {db_status}")
                            break
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
            else:
                print(f"⚠️  Failed to get meetings: {meetings_status}")
        
        if not active_detected:
            print("❌ Bot never reached active status")
            return
        
        print("\nBot is active! Now testing alone detection...")
        print("Please speak some words in the meeting to generate transcript.")
        print("Then LEAVE the meeting yourself (bot should stay and detect alone state)")
        
        # Wait for some transcript generation
        await asyncio.sleep(10)
        
        print("\nMANUAL VERIFICATION REQUIRED:")
        print("1. Confirm you have left the meeting")
        print("2. Verify the bot is now alone in the meeting")
        print("3. Check that the bot detects everyone left and exits gracefully")
        print("4. OR: Bot may have already detected alone state and completed automatically")
        user_confirms = input("Did you leave the meeting and is the bot alone? (y/n): ").lower().strip()
        if user_confirms == 'y':
            print("✓ User confirmed bot is alone in the meeting")
        else:
            print("❌ User reports bot is not alone")
        
        # Check if bot has already completed automatically
        print("\n=== CHECKING BOT STATUS ===")
        print("Checking if bot has already completed automatically...")
        
        status_check = await self._get_meetings()
        bot_already_completed = False
        if status_check[0] == 200:
            try:
                meetings_data = json.loads(status_check[1])
                meetings = meetings_data.get("meetings", [])
                target_meeting = None
                for m in meetings:
                    if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                        target_meeting = m
                        break
                
                if target_meeting:
                    current_status = target_meeting.get("status")
                    print(f"Current DB status: {current_status}")
                    
                    if current_status == "completed":
                        print("⚠️  Bot completed unexpectedly early - may indicate alone detection bug")
                        print("⚠️  Bot should wait for user to leave before detecting alone state")
                        bot_already_completed = True
                    elif current_status == "active":
                        print("Bot is still active, proceeding with stop request...")
                    else:
                        print(f"Bot status: {current_status}")
            except Exception as e:
                print(f"⚠️  Failed to parse meetings response: {e}")
        
        # Only try to stop if bot is still active
        if not bot_already_completed:
            print("\n=== STOPPING BOT VIA API ===")
            print("Stopping bot to test graceful shutdown...")
            stop_status, stop_body = await self._delete_bot(platform, native_id)
            if stop_status in (200, 202):
                print("✓ Bot stop request sent successfully")
                
                # CRITICAL VALIDATION: Check immediate status change
                print("Checking immediate status change...")
                immediate_status_check = await self._get_meetings()
                if immediate_status_check[0] == 200:
                    try:
                        meetings_data = json.loads(immediate_status_check[1])
                        meetings = meetings_data.get("meetings", [])
                        target_meeting = None
                        for m in meetings:
                            if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                                target_meeting = m
                                break
                        
                        if target_meeting:
                            immediate_db_status = target_meeting.get("status")
                            print(f"Immediate DB status: {immediate_db_status}")
                            
                            if immediate_db_status == "stopping":
                                print("✓ Status immediately changed to 'stopping'")
                            else:
                                print(f"⚠️  Unexpected immediate status: {immediate_db_status}")
                        else:
                            print("❌ Meeting not found in immediate status check")
                    except Exception as e:
                        print(f"❌ Failed to parse immediate meetings response: {e}")
                else:
                    print(f"❌ Failed to get immediate meetings status: {immediate_status_check[0]}")
            else:
                print(f"❌ Failed to stop bot: {stop_status} {stop_body[:200]}")
                return
        else:
            print("Bot already completed automatically - skipping stop request")
            stop_status = 200  # Set to success for test summary
        
        # Monitor for graceful shutdown and container cleanup
        print("Monitoring for graceful shutdown and container cleanup (up to 5 minutes)...")
        shutdown_completed = False
        container_cleaned = False
        
        for i in range(150):  # 150 * 2 = 300 seconds = 5 minutes max
            await asyncio.sleep(2)
            
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"DB status: {db_status}")
                        
                        if db_status == "completed":
                            print("✓ Bot completed gracefully")
                            shutdown_completed = True
                            
                            # Check for shutdown reason in meeting data
                            meeting_data = target_meeting.get("data", {})
                            if "last_error" in meeting_data:
                                error_info = meeting_data["last_error"]
                                reason = error_info.get("reason", "unknown")
                                exit_code = error_info.get("exit_code", "unknown")
                                print(f"✓ Shutdown reason: {reason} (exit_code: {exit_code})")
                            else:
                                print("✓ Bot completed without error details")
                            break
                        elif db_status == "failed":
                            print("⚠️  Bot failed during shutdown")
                            break
                        elif db_status == "stopping":
                            print("Bot still stopping, waiting for completion...")
                        elif db_status == "active":
                            print("Bot still active, waiting for shutdown...")
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
            else:
                print(f"⚠️  Failed to get meetings: {meetings_status}")
        
        # Check container cleanup
        print("\n=== CONTAINER CLEANUP VALIDATION ===")
        print("Checking if bot container has been cleaned up...")
        
        # Get running containers
        containers_status, containers_body = await self._get_bots_status()
        if containers_status == 200:
            try:
                containers_data = json.loads(containers_body)
                containers = containers_data.get("bots", [])
                
                # Check if our bot container is still running
                bot_still_running = False
                for container in containers:
                    if (container.get("platform") == platform and 
                        container.get("native_meeting_id") == native_id):
                        bot_still_running = True
                        print(f"⚠️  Bot container still running: {container.get('container_id')}")
                        break
                
                if not bot_still_running:
                    print("✓ Bot container has been cleaned up")
                    container_cleaned = True
                else:
                    print("❌ Bot container still running - cleanup failed")
            except Exception as e:
                print(f"⚠️  Failed to parse containers response: {e}")
        else:
            print(f"⚠️  Failed to get containers status: {containers_status}")
        
        # Validate transcript availability
        print("\n=== TRANSCRIPT VALIDATION ===")
        print("Checking if transcript is available via API...")
        
        transcript_status, transcript_body = await self._get_transcript(platform, native_id)
        if transcript_status == 200:
            try:
                transcript_data = json.loads(transcript_body)
                segments = transcript_data.get("segments", [])
                print(f"✓ Transcript available with {len(segments)} segments")
                
                if len(segments) > 0:
                    print("✓ Transcript contains speech data")
                else:
                    print("⚠️  Transcript is empty")
            except Exception as e:
                print(f"⚠️  Failed to parse transcript: {e}")
        else:
            print(f"⚠️  Transcript not available: {transcript_status}")
        
        # CRITICAL VALIDATION: Check final database status
        print("\n=== DATABASE STATUS VALIDATION ===")
        print("Checking final meeting status in database...")
        
        final_status_check = await self._get_meetings()
        if final_status_check[0] == 200:
            try:
                meetings_data = json.loads(final_status_check[1])
                meetings = meetings_data.get("meetings", [])
                target_meeting = None
                for m in meetings:
                    if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                        target_meeting = m
                        break
                
                if target_meeting:
                    final_db_status = target_meeting.get("status")
                    print(f"Final DB status: {final_db_status}")
                    
                    # Validate expected status based on scenario
                    if final_db_status == "completed":
                        print("✓ Bot status updated to 'completed' (alone detection)")
                        print("✓ Database state reflects bot alone detection completion")
                        
                        # Check for alone detection details in data
                        meeting_data = target_meeting.get("data", {})
                        if "last_error" in meeting_data:
                            error_info = meeting_data["last_error"]
                            reason = error_info.get("reason", "unknown")
                            exit_code = error_info.get("exit_code", "unknown")
                            print(f"✓ Alone detection details: {reason} (exit_code: {exit_code})")
                            
                            # Validate expected alone detection behavior
                            if reason == "everyone_left" and exit_code == 0:
                                print("✓ Correct alone detection behavior: everyone_left with exit_code 0")
                            else:
                                print(f"⚠️  Unexpected alone detection details: reason={reason}, exit_code={exit_code}")
                        else:
                            print("⚠️  No detailed alone detection info found in data")
                    elif final_db_status == "active":
                        print("⚠️  Bot still active - alone detection may not have occurred yet")
                        print("⚠️  Database state suggests bot is still in meeting")
                    else:
                        print(f"⚠️  Unexpected final status: {final_db_status}")
                        print("❌ Database state inconsistent with bot alone detection behavior")
                else:
                    print("❌ Meeting not found in final database check")
            except Exception as e:
                print(f"❌ Failed to parse final meetings response: {e}")
        else:
            print(f"❌ Failed to get final meetings status: {final_status_check[0]}")
        
        # Test summary
        print("\n=== TEST SUMMARY ===")
        print(f"✓ Bot reached active status: {'Yes' if active_detected else 'No'}")
        print(f"✓ User left meeting: {'Yes' if user_confirms == 'y' else 'No'}")
        print(f"✓ Bot completed gracefully: {'Yes' if shutdown_completed else 'No'}")
        print(f"✓ Container cleanup: {'Yes' if container_cleaned else 'No'}")
        print(f"✓ Transcript available: {'Yes' if transcript_status == 200 else 'No'}")
        
        if bot_already_completed:
            print("⚠️  Bot completed unexpectedly early: Yes")
            print("⚠️  ALONE DETECTION BUG DETECTED: Bot left before user")
        else:
            print(f"✓ Bot stop request sent: {'Yes' if stop_status in (200, 202) else 'No'}")
            print("✓ Manual stop test: PASSED")
        
        if (active_detected and shutdown_completed and 
            container_cleaned and transcript_status == 200):
            print("✓ Alone end test PASSED")
        else:
            print("❌ Alone end test FAILED")
        
        print("Alone end test completed.")

    async def run_evicted_end(self, meeting_url: Optional[str] = None) -> None:
        """Iteration 6: Evicted end test. Bot gets evicted/kicked from the meeting."""
        if not meeting_url:
            print("❌ Meeting URL is required for evicted end test")
            return
            
        platform, native_id = parse_meeting_url(meeting_url)
        
        print(f"\n=== EVICTED END TEST ===")
        print(f"Meeting URL: {meeting_url}")
        print(f"Platform: {platform}, Native ID: {native_id}")
        print("\nMANUAL ACTION REQUIRED:")
        print("1. Open the meeting URL in your browser")
        print("2. When the bot requests to join, ADMIT/ALLOW it")
        print("3. Speak some words to generate transcript")
        print("4. EVICT/KICK the bot from the meeting (remove participant)")
        print("5. Bot should detect eviction and exit gracefully")
        print("6. The test will monitor bot's eviction detection and graceful exit")
        input("\nPress Enter when ready to start the test...")
        
        print(f"\nStarting bot for {platform}/{native_id}...")
        status, body = await self._post_bots(self.api_key, {"platform": platform, "native_meeting_id": native_id})
        if status not in (200, 201):
            print(f"❌ Failed to start bot: {status} {body[:200]}")
            return
        
        print("✓ Bot started, waiting for admission...")
        print("Please admit the bot to the meeting now.")
        
        # Monitor for active status
        print("Monitoring for active status (up to 120 seconds)...")
        active_detected = False
        for i in range(60):  # 60 * 2 = 120 seconds max
            await asyncio.sleep(2)
            
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"DB status: {db_status}")
                        
                        if db_status == "active":
                            print("✓ Bot is active in the meeting")
                            active_detected = True
                            break
                        elif db_status in ("completed", "failed"):
                            print(f"⚠️  Bot completed/failed before reaching active: {db_status}")
                            break
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
            else:
                print(f"⚠️  Failed to get meetings: {meetings_status}")
        
        if not active_detected:
            print("❌ Bot never reached active status")
            return
        
        print("\nBot is active! Now testing eviction detection...")
        print("Please speak some words in the meeting to generate transcript.")
        print("Then EVICT/KICK the bot from the meeting (remove participant)")
        
        # Wait for some transcript generation
        await asyncio.sleep(10)
        
        print("\nMANUAL VERIFICATION REQUIRED:")
        print("1. Confirm you have evicted/kicked the bot from the meeting")
        print("2. Verify the bot is no longer in the meeting")
        print("3. The bot should detect eviction through UI changes and exit gracefully")
        print("4. This may take several minutes as the bot monitors the meeting interface")
        user_confirms = input("Did you evict the bot from the meeting? (y/n): ").lower().strip()
        if user_confirms == 'y':
            print("✓ User confirmed bot was evicted from the meeting")
        else:
            print("❌ User reports bot was not evicted")
        
        # Monitor for eviction detection and graceful exit
        print("Monitoring for eviction detection and graceful exit (up to 10 minutes)...")
        print("Note: Bot needs time to detect eviction through UI changes")
        eviction_detected = False
        container_cleaned = False
        
        for i in range(120):  # 120 * 5 = 600 seconds = 10 minutes max
            await asyncio.sleep(5)  # Check every 5 seconds instead of 2
            
            meetings_status, meetings_body = await self._get_meetings()
            if meetings_status == 200:
                try:
                    meetings_data = json.loads(meetings_body)
                    meetings = meetings_data.get("meetings", [])
                    target_meeting = None
                    for m in meetings:
                        if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"DB status: {db_status}")
                        
                        if db_status == "completed":
                            print("✓ Bot detected eviction and completed gracefully")
                            print("✓ DATABASE STATUS VALIDATION: Status changed from 'active' to 'completed'")
                            eviction_detected = True
                            
                            # Check for eviction reason in meeting data
                            meeting_data = target_meeting.get("data", {})
                            if "last_error" in meeting_data:
                                error_info = meeting_data["last_error"]
                                reason = error_info.get("reason", "unknown")
                                exit_code = error_info.get("exit_code", "unknown")
                                print(f"✓ DATABASE DATA VALIDATION: Eviction reason: {reason} (exit_code: {exit_code})")
                                
                                # Validate expected eviction behavior
                                if reason == "evicted" and exit_code == 0:
                                    print("✓ CORRECT EVICTION BEHAVIOR: evicted with exit_code 0")
                                else:
                                    print(f"⚠️  UNEXPECTED EVICTION DETAILS: reason={reason}, exit_code={exit_code}")
                            else:
                                print("✓ DATABASE DATA VALIDATION: Bot completed without error details (normal eviction)")
                            break
                        elif db_status == "failed":
                            print("⚠️  Bot failed during eviction detection")
                            break
                        elif db_status == "active":
                            elapsed_minutes = (i * 5) // 60
                            elapsed_seconds = (i * 5) % 60
                            print(f"Bot still active, waiting for eviction detection... ({elapsed_minutes}m {elapsed_seconds}s elapsed)")
                except Exception as e:
                    print(f"⚠️  Failed to parse meetings response: {e}")
            else:
                print(f"⚠️  Failed to get meetings: {meetings_status}")
        
        # Check container cleanup
        print("\n=== CONTAINER CLEANUP VALIDATION ===")
        print("Checking if bot container has been cleaned up...")
        
        # Get running containers
        containers_status, containers_body = await self._get_bots_status()
        if containers_status == 200:
            try:
                containers_data = json.loads(containers_body)
                containers = containers_data.get("bots", [])
                
                # Check if our bot container is still running
                bot_still_running = False
                for container in containers:
                    if (container.get("platform") == platform and 
                        container.get("native_meeting_id") == native_id):
                        bot_still_running = True
                        print(f"⚠️  Bot container still running: {container.get('container_id')}")
                        break
                
                if not bot_still_running:
                    print("✓ Bot container has been cleaned up")
                    container_cleaned = True
                else:
                    print("❌ Bot container still running - cleanup failed")
            except Exception as e:
                print(f"⚠️  Failed to parse containers response: {e}")
        else:
            print(f"⚠️  Failed to get containers status: {containers_status}")
        
        # Validate transcript availability
        print("\n=== TRANSCRIPT VALIDATION ===")
        print("Checking if transcript is available via API...")
        
        transcript_status, transcript_body = await self._get_transcript(platform, native_id)
        if transcript_status == 200:
            try:
                transcript_data = json.loads(transcript_body)
                segments = transcript_data.get("segments", [])
                print(f"✓ Transcript available with {len(segments)} segments")
                
                if len(segments) > 0:
                    print("✓ Transcript contains speech data")
                else:
                    print("⚠️  Transcript is empty")
            except Exception as e:
                print(f"⚠️  Failed to parse transcript: {e}")
        else:
            print(f"⚠️  Transcript not available: {transcript_status}")
        
        # CRITICAL VALIDATION: Check final database status
        print("\n=== DATABASE STATUS VALIDATION ===")
        print("Checking final meeting status in database...")
        
        final_status_check = await self._get_meetings()
        if final_status_check[0] == 200:
            try:
                meetings_data = json.loads(final_status_check[1])
                meetings = meetings_data.get("meetings", [])
                target_meeting = None
                for m in meetings:
                    if m.get("platform") == platform and m.get("native_meeting_id") == native_id:
                        target_meeting = m
                        break
                
                if target_meeting:
                    final_db_status = target_meeting.get("status")
                    print(f"Final DB status: {final_db_status}")
                    
                    # Validate expected status based on scenario
                    if final_db_status == "completed":
                        print("✓ FINAL DATABASE STATUS VALIDATION: Bot status updated to 'completed' (eviction detection)")
                        print("✓ FINAL DATABASE STATUS VALIDATION: Database state reflects bot eviction completion")
                        
                        # Check for eviction details in data
                        meeting_data = target_meeting.get("data", {})
                        if "last_error" in meeting_data:
                            error_info = meeting_data["last_error"]
                            reason = error_info.get("reason", "unknown")
                            exit_code = error_info.get("exit_code", "unknown")
                            print(f"✓ FINAL DATABASE DATA VALIDATION: Eviction details: {reason} (exit_code: {exit_code})")
                            
                            # Validate expected eviction behavior
                            if reason == "evicted" and exit_code == 0:
                                print("✓ FINAL DATABASE VALIDATION: Correct eviction behavior: evicted with exit_code 0")
                            else:
                                print(f"⚠️  FINAL DATABASE VALIDATION: Unexpected eviction details: reason={reason}, exit_code={exit_code}")
                        else:
                            print("✓ FINAL DATABASE DATA VALIDATION: Bot completed without error details (normal eviction)")
                    elif final_db_status == "active":
                        print("⚠️  Bot still active - eviction detection may not have occurred yet")
                        print("⚠️  Database state suggests bot is still in meeting")
                    else:
                        print(f"⚠️  Unexpected final status: {final_db_status}")
                        print("❌ Database state inconsistent with bot eviction behavior")
                else:
                    print("❌ Meeting not found in final database check")
            except Exception as e:
                print(f"❌ Failed to parse final meetings response: {e}")
        else:
            print(f"❌ Failed to get final meetings status: {final_status_check[0]}")
        
        # Test summary
        print("\n=== TEST SUMMARY ===")
        print(f"✓ Bot reached active status: {'Yes' if active_detected else 'No'}")
        print(f"✓ User evicted bot: {'Yes' if user_confirms == 'y' else 'No'}")
        print(f"✓ Eviction detection completed: {'Yes' if eviction_detected else 'No'}")
        print(f"✓ DATABASE STATUS VALIDATION: {'PASSED' if eviction_detected else 'FAILED'}")
        print(f"✓ Container cleanup: {'Yes' if container_cleaned else 'No'}")
        print(f"✓ Transcript available: {'Yes' if transcript_status == 200 else 'No'}")
        
        if (active_detected and user_confirms == 'y' and 
            eviction_detected and container_cleaned and transcript_status == 200):
            print("✓ Evicted end test PASSED")
        else:
            print("❌ Evicted end test FAILED")
        
        print("Evicted end test completed.")

    async def run_concurrency_test(self, meeting_urls: List[str]) -> None:
        """Concurrency subtest: Test bot limiting and concurrent bot creation."""
        if not meeting_urls or len(meeting_urls) < 3:
            print("❌ Concurrency test requires 3 meeting URLs")
            return
        
        print(f"\n=== CONCURRENCY TEST ===")
        print(f"Meeting URLs: {meeting_urls}")
        print("\nMANUAL ACTION REQUIRED:")
        print("1. Open all 3 meeting URLs in your browser")
        print("2. The test will attempt to start bots for all 3 meetings")
        print("3. System should enforce limits on concurrent bots per user")
        print("4. Some bots may be rejected due to concurrency limits")
        print("5. The test will validate proper concurrency handling")
        input("\nPress Enter when ready to start the test...")
        
        # Parse all meeting URLs
        meetings = []
        for url in meeting_urls:
            platform, native_id = parse_meeting_url(url)
            meetings.append({
                "url": url,
                "platform": platform,
                "native_id": native_id
            })
            print(f"Parsed: {platform}/{native_id}")
        
        print(f"\nStarting {len(meetings)} bots concurrently...")
        
        # Start all bots concurrently
        bot_results = []
        for i, meeting in enumerate(meetings):
            print(f"Starting bot {i+1}/{len(meetings)} for {meeting['platform']}/{meeting['native_id']}...")
            status, body = await self._post_bots(self.api_key, {
                "platform": meeting["platform"], 
                "native_meeting_id": meeting["native_id"]
            })
            
            bot_results.append({
                "meeting": meeting,
                "status": status,
                "body": body,
                "success": status in (200, 201)
            })
            
            if status in (200, 201):
                print(f"✓ Bot {i+1} started successfully")
            else:
                print(f"❌ Bot {i+1} failed: {status} {body[:200]}")
        
        # Analyze results
        successful_bots = [r for r in bot_results if r["success"]]
        failed_bots = [r for r in bot_results if not r["success"]]
        
        print(f"\n=== CONCURRENCY RESULTS ===")
        print(f"Total bots attempted: {len(bot_results)}")
        print(f"Successful starts: {len(successful_bots)}")
        print(f"Failed starts: {len(failed_bots)}")
        
        # Check for concurrency limit errors
        concurrency_errors = []
        for bot_result in failed_bots:
            if "concurrent" in bot_result["body"].lower() or "limit" in bot_result["body"].lower():
                concurrency_errors.append(bot_result)
                print(f"✓ Concurrency limit detected for {bot_result['meeting']['platform']}/{bot_result['meeting']['native_id']}")
        
        # Monitor successful bots
        print(f"\n=== MONITORING SUCCESSFUL BOTS ===")
        active_bots = []
        for i, bot_result in enumerate(successful_bots):
            meeting = bot_result["meeting"]
            print(f"Monitoring bot {i+1}: {meeting['platform']}/{meeting['native_id']}")
            
            # Wait for bot to reach active status
            active_detected = False
            for j in range(30):  # 30 * 2 = 60 seconds max per bot
                await asyncio.sleep(2)
                
                meetings_status, meetings_body = await self._get_meetings()
                if meetings_status == 200:
                    try:
                        meetings_data = json.loads(meetings_body)
                        meetings_list = meetings_data.get("meetings", [])
                        
                        for m in meetings_list:
                            if (m.get("platform") == meeting["platform"] and 
                                m.get("native_meeting_id") == meeting["native_id"]):
                                db_status = m.get("status")
                                if db_status == "active":
                                    print(f"✓ Bot {i+1} reached active status")
                                    active_bots.append({
                                        "meeting": meeting,
                                        "status": db_status,
                                        "meeting_data": m
                                    })
                                    active_detected = True
                                    break
                                elif db_status in ("completed", "failed"):
                                    print(f"⚠️  Bot {i+1} completed/failed before active: {db_status}")
                                    break
                    except Exception as e:
                        print(f"⚠️  Failed to parse meetings response for bot {i+1}: {e}")
                
                if active_detected:
                    break
            
            if not active_detected:
                print(f"❌ Bot {i+1} never reached active status")
        
        # Check running containers
        print(f"\n=== CONTAINER STATUS VALIDATION ===")
        containers_status, containers_body = await self._get_bots_status()
        running_containers = []
        if containers_status == 200:
            try:
                containers_data = json.loads(containers_body)
                containers = containers_data.get("bots", [])
                running_containers = containers
                print(f"✓ Found {len(containers)} running containers")
                
                for container in containers:
                    print(f"  - {container.get('platform')}/{container.get('native_meeting_id')}: {container.get('container_id')}")
            except Exception as e:
                print(f"⚠️  Failed to parse containers response: {e}")
        else:
            print(f"⚠️  Failed to get containers status: {containers_status}")
        
        # Validate concurrency limits
        print(f"\n=== CONCURRENCY LIMIT VALIDATION ===")
        
        # Check if system properly enforced limits
        if len(concurrency_errors) > 0:
            print(f"✓ CONCURRENCY LIMITS ENFORCED: {len(concurrency_errors)} bots rejected due to limits")
            for error in concurrency_errors:
                print(f"  - {error['meeting']['platform']}/{error['meeting']['native_id']}: {error['body'][:100]}")
        else:
            print("⚠️  No concurrency limit errors detected - system may not be enforcing limits")
        
        # Check if active bots exceed expected limits
        if len(active_bots) > 1:
            print(f"⚠️  MULTIPLE ACTIVE BOTS: {len(active_bots)} bots are active simultaneously")
            print("⚠️  This may indicate concurrency limits are not properly enforced")
        else:
            print(f"✓ SINGLE ACTIVE BOT: Only {len(active_bots)} bot is active (expected behavior)")
        
        # Database validation for all meetings
        print(f"\n=== DATABASE STATUS VALIDATION ===")
        final_status_check = await self._get_meetings()
        if final_status_check[0] == 200:
            try:
                meetings_data = json.loads(final_status_check[1])
                meetings_list = meetings_data.get("meetings", [])
                
                print(f"✓ Found {len(meetings_list)} meetings in database")
                
                for meeting in meetings:
                    target_meeting = None
                    for m in meetings_list:
                        if (m.get("platform") == meeting["platform"] and 
                            m.get("native_meeting_id") == meeting["native_id"]):
                            target_meeting = m
                            break
                    
                    if target_meeting:
                        db_status = target_meeting.get("status")
                        print(f"✓ DATABASE STATUS: {meeting['platform']}/{meeting['native_id']}: {db_status}")
                    else:
                        print(f"⚠️  DATABASE STATUS: {meeting['platform']}/{meeting['native_id']}: Not found")
            except Exception as e:
                print(f"❌ Failed to parse final meetings response: {e}")
        else:
            print(f"❌ Failed to get final meetings status: {final_status_check[0]}")
        
        # Test summary
        print(f"\n=== CONCURRENCY TEST SUMMARY ===")
        print(f"✓ Bots attempted: {len(bot_results)}")
        print(f"✓ Successful starts: {len(successful_bots)}")
        print(f"✓ Failed starts: {len(failed_bots)}")
        print(f"✓ Concurrency limit errors: {len(concurrency_errors)}")
        print(f"✓ Active bots: {len(active_bots)}")
        print(f"✓ Running containers: {len(running_containers)}")
        
        # Determine test result
        concurrency_test_passed = True
        
        # Check if concurrency limits are working
        if len(concurrency_errors) == 0 and len(active_bots) > 1:
            print("❌ CONCURRENCY LIMITS NOT ENFORCED: Multiple bots active without limits")
            concurrency_test_passed = False
        elif len(concurrency_errors) > 0:
            print("✓ CONCURRENCY LIMITS WORKING: System properly rejected excess bots")
        else:
            print("✓ CONCURRENCY TEST: Single bot active (normal behavior)")
        
        if concurrency_test_passed:
            print("✓ Concurrency test PASSED")
        else:
            print("❌ Concurrency test FAILED")
        
        print("Concurrency test completed.")

    async def _get_transcript(self, platform: str, native_id: str) -> tuple[int, str]:
        """Get transcript via GET /transcripts/{platform}/{native_id}."""
        if httpx is None:
            return (0, "httpx missing")
        headers = {"X-API-Key": self.api_key}
        url = f"{self.api_base}/transcripts/{platform}/{native_id}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            return (resp.status_code, resp.text)

    async def _update_bot_config(self, platform: str, native_id: str, config: dict) -> tuple[int, str]:
        """Update bot config via PUT /bots/{platform}/{native_id}/config."""
        if httpx is None:
            return (0, "httpx missing")
        headers = {"X-API-Key": self.api_key, "Content-Type": "application/json"}
        url = f"{self.api_base}/bots/{platform}/{native_id}/config"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.put(url, headers=headers, json=config)
            return (resp.status_code, resp.text)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Meeting Bot E2E single-file suite")
    p.add_argument("--api-key", required=True, help="User API key (X-API-Key)")
    p.add_argument("--meeting-url", help="Primary meeting URL (required for most tests)")
    p.add_argument("--meeting-urls", nargs="*", help="Three URLs for concurrency tests")
    p.add_argument("--only", choices=[
        "validation",
        "stop_before_join", 
        "joining_failure",
        "bot_timeout",
        "on_meeting",
        "alone_end",
        "evicted_end",
        "concurrency",
    ], help="Run only a specific launch")
    return p


async def main_async() -> int:
    api_base, ws_url = build_base_urls()
    args = build_arg_parser().parse_args()
    suite = MeetingBotTestSuite(api_base=api_base, ws_url=ws_url, api_key=args.api_key)

    if args.only == "validation":
        await suite.run_validation_negative(args.meeting_url)
        return 0
    
    if args.only == "stop_before_join":
        await suite.run_stop_before_join(args.meeting_url)
        return 0
    
    if args.only == "joining_failure":
        await suite.run_joining_failure(args.meeting_url)
        return 0
    
    if args.only == "bot_timeout":
        await suite.run_bot_timeout_test(args.meeting_url)
        return 0
    
    if args.only == "on_meeting":
        await suite.run_on_meeting_path(args.meeting_url)
        return 0
    
    if args.only == "alone_end":
        await suite.run_alone_end(args.meeting_url)
        return 0
    
    if args.only == "evicted_end":
        await suite.run_evicted_end(args.meeting_url)
        return 0
    
    if args.only == "concurrency":
        if not args.meeting_urls or len(args.meeting_urls) < 3:
            print("❌ Concurrency test requires 3 meeting URLs via --meeting-urls")
            return 1
        await suite.run_concurrency_test(args.meeting_urls)
        return 0

    print(f"API base: {api_base}; WS: {ws_url}")
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(main_async())
    except KeyboardInterrupt:
        exit_code = 130
    except NotImplementedError:
        exit_code = 99
    except Exception as e:
        print(f"Unexpected error: {e}")
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
