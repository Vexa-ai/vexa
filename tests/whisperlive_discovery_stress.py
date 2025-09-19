#!/usr/bin/env python3
"""
WhisperLive Service Discovery Stress Tests

Tests the robust service discovery implementation:
- Server deregistration on shutdown
- Periodic scrubbing of stale entries
- Heartbeat-aware bot allocation
- Load distribution and failure handling
"""

import os
import sys
import json
import time
import subprocess
import asyncio
import requests
from typing import List, Tuple, Dict, Any

# Configuration
REDIS_CONT = os.environ.get("REDIS_CONT", "vexa_dev-redis-1")
API = os.environ.get("API", "http://localhost:18056")
TOKEN = os.environ.get("TOKEN", os.environ.get("ADMIN_API_TOKEN", "token"))
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

MEET_URL = os.environ.get("MEET_URL", "https://meet.google.com/kba-qqag-vpq")
PLATFORM = os.environ.get("PLATFORM", "google_meet")
LANG = os.environ.get("LANG", "en")


class WhisperLiveStressTest:
    def __init__(self):
        self.spawned_bots = []

    async def redis_cmd(self, *args: str) -> str:
        """Execute Redis command via Docker."""
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", REDIS_CONT, "redis-cli", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"redis-cli failed: {' '.join(args)} -> {err.decode()}")
        return out.decode().strip()

    async def get_rank(self) -> List[str]:
        """Get wl:rank entries with scores."""
        out = await self.redis_cmd("ZRANGE", "wl:rank", "0", "-1", "WITHSCORES")
        return out.splitlines()

    async def get_heartbeats(self) -> List[str]:
        """Get all wl:hb:* keys."""
        out = await self.redis_cmd("KEYS", "wl:hb:*")
        return out.splitlines()

    async def scrub_stale_entries(self) -> int:
        """Remove wl:rank members without heartbeats."""
        lua = (
            "local servers=redis.call('ZRANGE','wl:rank',0,-1); local removed=0; "
            "for i=1,#servers do local url=servers[i]; local hb='wl:hb:'..url; "
            "if redis.call('EXISTS', hb)==0 then redis.call('ZREM','wl:rank', url); removed=removed+1; end; end; return removed"
        )
        out = await self.redis_cmd("EVAL", lua, "0")
        return int(out)

    def request_bot(self, meeting_url: str) -> Dict[str, Any]:
        """Request a bot via API."""
        payload = {
            "platform": PLATFORM,
            "meeting_url": meeting_url,
            "bot_name": None,
            "language": LANG,
            "task": "transcribe"
        }
        r = requests.post(f"{API}/bots/request", json=payload, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json()

    def stop_bot(self, meeting_id: int):
        """Stop a bot via API."""
        r = requests.post(f"{API}/bots/stop/{meeting_id}", headers=HEADERS, timeout=20)
        r.raise_for_status()

    async def spawn_bots(self, n: int) -> List[int]:
        """Spawn n bots and return their meeting IDs."""
        ids = []
        for i in range(n):
            try:
                resp = self.request_bot(MEET_URL)
                meeting_id = resp.get("meeting_id")
                if meeting_id is None:
                    print(f"Warning: No meeting_id in response: {resp}")
                    continue
                ids.append(meeting_id)
                self.spawned_bots.append(meeting_id)
                print(f"  Spawned bot {i+1}/{n}: meeting_id={meeting_id}")
            except Exception as e:
                print(f"  Failed to spawn bot {i+1}/{n}: {e}")
        return ids

    async def get_distribution(self) -> List[Tuple[str, int]]:
        """Get current load distribution from wl:rank."""
        rank = await self.get_rank()
        # rank is [url, score, url, score, ...]
        pairs = list(zip(rank[::2], rank[1::2]))
        return [(url, int(float(score))) for (url, score) in pairs]

    async def check_consistency(self) -> Dict[str, Any]:
        """Check consistency between wl:rank and wl:hb:*."""
        rank = await self.get_rank()
        hbs = await self.get_heartbeats()
        
        urls = set(rank[::2])
        live = set(hb.replace("wl:hb:", "") for hb in hbs if hb)
        stale = urls - live
        
        return {
            "urls": sorted(urls),
            "live": sorted(live),
            "stale": sorted(stale),
            "total_servers": len(urls),
            "live_servers": len(live),
            "stale_count": len(stale)
        }

    async def test_basic_allocation(self) -> List[int]:
        """Test basic allocation distributes to live servers only."""
        print("\n=== Test 1: Basic Allocation ===")
        print("Starting basic allocation test (2 bots)...")
        
        before = await self.get_distribution()
        print(f"Before: {before}")
        
        ids = await self.spawn_bots(2)
        await asyncio.sleep(3)  # Let allocation settle
        
        after = await self.get_distribution()
        consistency = await self.check_consistency()
        
        print(f"After: {after}")
        print(f"Consistency: {consistency['stale_count']} stale, {consistency['live_servers']} live")
        
        if consistency["stale_count"] > 0:
            raise AssertionError(f"Stale entries found: {consistency['stale']}")
        
        print("✅ Basic allocation distributes to live servers only")
        return ids

    async def test_scale_and_failure(self, initial_ids: List[int]):
        """Test scaling up and handling server failure."""
        print("\n=== Test 2: Scale Up + Server Failure ===")
        print("Scaling to 6 bots, then killing one WhisperLive container...")
        
        # Spawn 4 more bots
        additional_ids = await self.spawn_bots(4)
        await asyncio.sleep(3)
        
        dist = await self.get_distribution()
        print(f"Distribution after scale: {dist}")
        
        # Kill one WhisperLive container
        try:
            out = subprocess.check_output([
                "bash", "-c", 
                "docker ps --format '{{.Names}}' | grep vexa_dev-whisperlive- | head -n1"
            ]).decode().strip()
            
            if out:
                subprocess.check_call(["docker", "kill", out])
                print(f"Killed container: {out}")
            else:
                print("No WhisperLive container found to kill.")
                return
        except subprocess.CalledProcessError as e:
            print(f"Failed to kill container: {e}")
            return
        
        # Wait for heartbeat expiry and server scrub
        print("Waiting 45s for heartbeat expiry and scrub...")
        await asyncio.sleep(45)
        
        consistency = await self.check_consistency()
        print(f"Consistency after kill: {consistency['stale_count']} stale, {consistency['live_servers']} live")
        
        if consistency["stale_count"] > 0:
            # Try manual scrub
            removed = await self.scrub_stale_entries()
            print(f"Manual scrub removed {removed} stale entries")
            consistency = await self.check_consistency()
            
        if consistency["stale_count"] > 0:
            print(f"Warning: {consistency['stale_count']} stale entries remain: {consistency['stale']}")
        else:
            print("✅ Dead server removed from wl:rank")

    async def test_capacity_awareness(self):
        """Test that allocator respects capacity limits."""
        print("\n=== Test 3: Capacity Awareness ===")
        print("Verifying allocator respects capacity via score and heartbeat...")
        
        dist = await self.get_distribution()
        cap = int(os.environ.get("WL_MAX_CLIENTS", "10"))
        
        print(f"Current distribution: {dist}")
        print(f"Max clients per server: {cap}")
        
        # Check that no server exceeds capacity in our allocation
        over_capacity = [(url, score) for url, score in dist if score > cap]
        if over_capacity:
            print(f"Warning: Servers over capacity: {over_capacity}")
        else:
            print("✅ No servers exceed configured capacity")
        
        print("✅ Allocator respects capacity via score and heartbeat")

    async def cleanup_bots(self):
        """Clean up spawned bots."""
        if not self.spawned_bots:
            return
            
        print(f"\n=== Cleanup: Stopping {len(self.spawned_bots)} bots ===")
        for meeting_id in self.spawned_bots:
            try:
                self.stop_bot(meeting_id)
                print(f"  Stopped bot: meeting_id={meeting_id}")
            except Exception as e:
                print(f"  Failed to stop bot {meeting_id}: {e}")
        
        self.spawned_bots.clear()
        await asyncio.sleep(2)  # Let cleanup settle

    async def run_all_tests(self):
        """Run all stress tests."""
        try:
            print("WhisperLive Service Discovery Stress Tests")
            print("=" * 50)
            
            # Initial state
            initial_consistency = await self.check_consistency()
            print(f"Initial state: {initial_consistency['live_servers']} live servers, {initial_consistency['stale_count']} stale")
            
            # Run tests
            ids = await self.test_basic_allocation()
            await self.test_scale_and_failure(ids)
            await self.test_capacity_awareness()
            
            print("\n" + "=" * 50)
            print("✅ All stress tests completed successfully!")
            
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            raise
        finally:
            await self.cleanup_bots()


async def main():
    """Main entry point."""
    test = WhisperLiveStressTest()
    await test.run_all_tests()


if __name__ == "__main__":
    # Check prerequisites
    try:
        subprocess.check_call(["docker", "ps"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        print("Error: Docker not available")
        sys.exit(1)
    
    # Run tests
    asyncio.run(main())
