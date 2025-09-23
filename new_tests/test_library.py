#!/usr/bin/env python3
"""
Simple test script to verify the Vexa Test Library functionality.
"""

import os
import sys
import time

# Add the new_tests directory to the path
sys.path.append('./new_tests')

from test_suite import TestSuite
from bot import Bot


def test_basic_functionality():
    """Test basic functionality without requiring actual API calls."""
    print("Testing basic functionality...")
    
    # Test Bot class initialization
    print("1. Testing Bot class...")
    
    # Mock user client (we won't actually use it for API calls)
    class MockClient:
        def __init__(self):
            self.user_id = 123
    
    mock_client = MockClient()
    
    # Test Bot initialization
    bot = Bot(
        user_client=mock_client,
        meeting_url="https://teams.live.com/meet/1234567890123?p=TestPasscode",
        bot_id="test_bot_1"
    )
    
    print(f"   ✓ Bot created: {bot.bot_id}")
    print(f"   ✓ Platform parsed: {bot.platform}")
    print(f"   ✓ Meeting ID parsed: {bot.native_meeting_id}")
    print(f"   ✓ Passcode parsed: {bot.passcode}")
    
    # Test Bot stats
    stats = bot.get_stats()
    print(f"   ✓ Stats retrieved: {len(stats)} fields")
    
    # Test TestSuite initialization
    print("\n2. Testing TestSuite class...")
    
    test_suite = TestSuite(
        base_url="http://localhost:18056",
        admin_api_key="test_key",
        poll_interval=1.0
    )
    
    print(f"   ✓ TestSuite created with {test_suite.poll_interval}s interval")
    
    # Test snapshot functionality (without actual bots)
    print("\n3. Testing snapshot functionality...")
    
    snapshot = test_suite.snapshot()
    print(f"   ✓ Snapshot created with timestamp: {snapshot['timestamp']}")
    print(f"   ✓ Snapshot contains {len(snapshot['bots'])} bots")
    
    # Test pandas parsing
    print("\n4. Testing pandas parsing...")
    
    rows = test_suite.parse_for_pandas(snapshot)
    print(f"   ✓ Parsed {len(rows)} rows for pandas")
    
    if rows:
        print(f"   ✓ Sample row keys: {list(rows[0].keys())}")
    
    print("\n✅ All basic functionality tests passed!")
    return True


def test_url_parsing():
    """Test URL parsing functionality."""
    print("\nTesting URL parsing...")
    
    from vexa_client.vexa import parse_url
    
    test_urls = [
        ("https://teams.live.com/meet/9398850880426?p=RBZCWdxyp85TpcKna8", "teams", "9398850880426", "RBZCWdxyp85TpcKna8"),
        ("https://meet.google.com/abc-defg-hij", "google_meet", "abc-defg-hij", None),
        ("https://teams.live.com/meet/1234567890123", "teams", "1234567890123", ""),
    ]
    
    for url, expected_platform, expected_id, expected_passcode in test_urls:
        platform, meeting_id, passcode = parse_url(url)
        
        assert platform == expected_platform, f"Platform mismatch for {url}"
        assert meeting_id == expected_id, f"Meeting ID mismatch for {url}"
        assert passcode == expected_passcode, f"Passcode mismatch for {url}"
        
        print(f"   ✓ {url} -> {platform}, {meeting_id}, {passcode}")
    
    print("✅ URL parsing tests passed!")
    return True


if __name__ == "__main__":
    print("Vexa Test Library - Basic Functionality Test")
    print("=" * 50)
    
    try:
        test_basic_functionality()
        test_url_parsing()
        
        print("\n" + "=" * 50)
        print("🎉 All tests passed! The Vexa Test Library is ready to use.")
        print("\nNext steps:")
        print("1. Set up your ADMIN_API_TOKEN environment variable")
        print("2. Update BASE_URL in demo_notebook.ipynb")
        print("3. Run the demo notebook to test with real API calls")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
