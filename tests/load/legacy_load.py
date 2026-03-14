"""
TestSuite class for managing multiple users and bots in Vexa testing scenarios.

This class provides:
- User creation and management
- Random user-meeting mapping
- Bot lifecycle management
- Snapshot and pandas integration for notebook use
"""

import time
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import pandas as pd

import sys
import os
# Use the fixed PyPI client
sys.path.insert(0, '/Users/dmitriygrankin/dev/vexa-pypi-client')
from vexa_client import VexaClient
from bot import Bot


def create_thread_safe_session():
    """
    Create a thread-safe requests session with proper SSL handling.
    
    Returns:
        requests.Session with thread-safe configuration
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    
    session = requests.Session()
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    
    # Create adapter with retry strategy
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=10,
        pool_maxsize=20
    )
    
    # Mount adapter for both HTTP and HTTPS
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # Set headers
    session.headers.update({
        'User-Agent': 'Vexa-TestSuite/1.0',
        'Connection': 'close'  # Prevent connection reuse issues
    })
    
    return session


class TestSuite:
    """
    A comprehensive test suite for managing multiple Vexa users and bots.
    
    Features:
    - Create multiple users with individual API keys
    - Random mapping of users to meetings
    - Bot lifecycle management
    - Background monitoring with timestamps
    - Snapshot functionality for pandas integration
    """
    
    def __init__(self, 
                 base_url: str = "http://localhost:18056",
                 admin_api_key: Optional[str] = None,
                 use_thread_safe_sessions: bool = True):
        """
        Initialize the TestSuite.
        
        Args:
            base_url: Base URL for the Vexa API
            admin_api_key: Admin API key for user creation
            use_thread_safe_sessions: Whether to use thread-safe session management
        """
        self.base_url = base_url
        self.admin_api_key = admin_api_key
        self.use_thread_safe_sessions = use_thread_safe_sessions
        
        # Initialize admin client if API key provided
        self.admin_client = None
        if admin_api_key:
            self.admin_client = self._create_vexa_client(
                base_url=base_url,
                admin_key=admin_api_key
            )
        
        # Test suite state
        self.users: List[VexaClient] = []
        self.bots: List[Bot] = []
        self.user_meeting_mapping: Dict[int, str] = {}  # user_index -> meeting_url
    
    def _create_vexa_client(self, base_url: str, api_key: Optional[str] = None, 
                           admin_key: Optional[str] = None, user_id: Optional[str] = None) -> VexaClient:
        """
        Create a VexaClient instance.
        
        Args:
            base_url: Base URL for the Vexa API
            api_key: User API key
            admin_key: Admin API key
            user_id: User ID (not used by VexaClient constructor)
            
        Returns:
            VexaClient instance
        """
        return VexaClient(
            base_url=base_url,
            api_key=api_key,
            admin_key=admin_key
        )
        
    def create_users(self, num_users: int) -> List[VexaClient]:
        """
        Create the specified number of users and return their client instances.
        
        Args:
            num_users: Number of users to create
            
        Returns:
            List of VexaClient instances for the created users
        """
        if not self.admin_client:
            raise Exception("Admin API key required for user creation. Set admin_api_key in constructor.")
        
        print(f"Creating {num_users} users...")
        self.users = []
        
        for i in range(num_users):
            try:
                # Create user with unique email
                user_data = self.admin_client.create_user_and_set_id(
                    email=f"test_user_{i}_{random.randint(1000, 9999)}@example.com",
                    name=f"Test User {i}",
                    max_concurrent_bots=2  # Allow multiple bots per user
                )
                
                # Create API token for the user
                token_info = self.admin_client.create_token()
                user_api_key = token_info['token']
                
                # Create user client
                user_client = self._create_vexa_client(
                    base_url=self.base_url,
                    api_key=user_api_key,
                    user_id=user_data['id']
                )
                
                self.users.append(user_client)
                print(f"Created user {i+1}/{num_users}: {user_data['email']}")
                
            except Exception as e:
                print(f"Failed to create user {i+1}: {e}")
                raise
        
        print(f"Successfully created {len(self.users)} users")
        return self.users
    
    def add_users(self, additional_users: int) -> List[VexaClient]:
        """
        Add additional users during runtime without affecting existing users.
        
        Args:
            additional_users: Number of additional users to create
            
        Returns:
            List of newly created VexaClient instances
        """
        if not self.admin_client:
            raise Exception("Admin API key required for user creation. Set admin_api_key in constructor.")
        
        if additional_users <= 0:
            raise ValueError("additional_users must be greater than 0")
        
        print(f"Adding {additional_users} additional users...")
        new_users = []
        start_index = len(self.users)
        
        for i in range(additional_users):
            try:
                # Create user with unique email using current user count as base
                user_data = self.admin_client.create_user_and_set_id(
                    email=f"test_user_{start_index + i}_{random.randint(1000, 9999)}@example.com",
                    name=f"Test User {start_index + i}",
                    max_concurrent_bots=2  # Allow multiple bots per user
                )
                
                # Create API token for the user
                token_info = self.admin_client.create_token()
                user_api_key = token_info['token']
                
                # Create user client
                user_client = self._create_vexa_client(
                    base_url=self.base_url,
                    api_key=user_api_key,
                    user_id=user_data['id']
                )
                
                self.users.append(user_client)
                new_users.append(user_client)
                print(f"Added user {start_index + i + 1}: {user_data['email']}")
                
            except Exception as e:
                print(f"Failed to create additional user {start_index + i + 1}: {e}")
                raise
        
        print(f"Successfully added {len(new_users)} users. Total users: {len(self.users)}")
        return new_users
    
    def create_random_mapping(self, meeting_urls: List[str]) -> Dict[int, str]:
        """
        Create a random mapping of users to meetings.
        
        Args:
            meeting_urls: List of meeting URLs to distribute among users
            
        Returns:
            Dictionary mapping user_index -> meeting_url
        """
        if not self.users:
            raise Exception("No users created. Call create_users() first.")
        
        print(f"Creating random mapping for {len(self.users)} users and {len(meeting_urls)} meetings...")
        
        # Create random mapping
        self.user_meeting_mapping = {}
        available_meetings = meeting_urls.copy()
        
        for user_index in range(len(self.users)):
            if available_meetings:
                # Randomly select a meeting for this user
                meeting_url = random.choice(available_meetings)
                self.user_meeting_mapping[user_index] = meeting_url
                
                # Optionally remove the meeting to avoid duplicates
                # (comment out the next line if you want to allow multiple users per meeting)
                available_meetings.remove(meeting_url)
            else:
                # If we run out of meetings, cycle through them
                meeting_url = random.choice(meeting_urls)
                self.user_meeting_mapping[user_index] = meeting_url
        
        print(f"Created mapping: {self.user_meeting_mapping}")
        return self.user_meeting_mapping
    
    def extend_mapping(self, meeting_urls: List[str]) -> Dict[int, str]:
        """
        Extend the existing user-meeting mapping for newly added users.
        
        Args:
            meeting_urls: List of meeting URLs to distribute among new users
            
        Returns:
            Updated dictionary mapping user_index -> meeting_url
        """
        if not self.users:
            raise Exception("No users created. Call create_users() first.")
        
        if not self.user_meeting_mapping:
            raise Exception("No existing mapping found. Call create_random_mapping() first.")
        
        # Find users that don't have mappings yet
        existing_mapped_users = set(self.user_meeting_mapping.keys())
        all_user_indices = set(range(len(self.users)))
        unmapped_users = all_user_indices - existing_mapped_users
        
        if not unmapped_users:
            print("All users already have meeting mappings")
            return self.user_meeting_mapping
        
        print(f"Extending mapping for {len(unmapped_users)} unmapped users with {len(meeting_urls)} meetings...")
        
        # Create mapping for unmapped users
        available_meetings = meeting_urls.copy()
        
        for user_index in sorted(unmapped_users):
            if available_meetings:
                # Randomly select a meeting for this user
                meeting_url = random.choice(available_meetings)
                self.user_meeting_mapping[user_index] = meeting_url
                
                # Optionally remove the meeting to avoid duplicates
                # (comment out the next line if you want to allow multiple users per meeting)
                available_meetings.remove(meeting_url)
            else:
                # If we run out of meetings, cycle through them
                meeting_url = random.choice(meeting_urls)
                self.user_meeting_mapping[user_index] = meeting_url
        
        print(f"Extended mapping: {self.user_meeting_mapping}")
        return self.user_meeting_mapping
    
    def create_bots(self, bot_name_prefix: str = "TestBot") -> List[Bot]:
        """
        Create Bot instances based on the user-meeting mapping.
        
        Args:
            bot_name_prefix: Prefix for bot names
            
        Returns:
            List of Bot instances
        """
        if not self.user_meeting_mapping:
            raise Exception("No user-meeting mapping created. Call create_random_mapping() first.")
        
        print(f"Creating {len(self.user_meeting_mapping)} bots...")
        self.bots = []
        
        for user_index, meeting_url in self.user_meeting_mapping.items():
            user_client = self.users[user_index]
            bot = Bot(
                user_client=user_client,
                meeting_url=meeting_url,
                bot_id=f"{bot_name_prefix}_{user_index}"
            )
            self.bots.append(bot)
            print(f"Created bot {bot.bot_id} for user {user_index} -> {meeting_url}")
        
        print(f"Successfully created {len(self.bots)} bots")
        return self.bots
    
    def add_bots(self, meeting_urls: List[str], bot_name_prefix: str = "TestBot") -> List[Bot]:
        """
        Create additional Bot instances for newly added users during runtime.
        
        Args:
            meeting_urls: List of meeting URLs to distribute among new users
            bot_name_prefix: Prefix for bot names
            
        Returns:
            List of newly created Bot instances
        """
        if not self.users:
            raise Exception("No users created. Call create_users() first.")
        
        # Extend the mapping for new users
        self.extend_mapping(meeting_urls)
        
        # Find users that don't have bots yet
        existing_bot_users = set()
        for bot in self.bots:
            # Extract user index from bot_id (assuming format "prefix_index")
            try:
                user_index = int(bot.bot_id.split('_')[-1])
                existing_bot_users.add(user_index)
            except (ValueError, IndexError):
                continue
        
        # Find unmapped users that need bots
        unmapped_users = set()
        for user_index in self.user_meeting_mapping.keys():
            if user_index not in existing_bot_users:
                unmapped_users.add(user_index)
        
        if not unmapped_users:
            print("All users already have bots")
            return []
        
        print(f"Creating {len(unmapped_users)} additional bots...")
        new_bots = []
        
        for user_index in sorted(unmapped_users):
            if user_index in self.user_meeting_mapping:
                user_client = self.users[user_index]
                meeting_url = self.user_meeting_mapping[user_index]
                bot = Bot(
                    user_client=user_client,
                    meeting_url=meeting_url,
                    bot_id=f"{bot_name_prefix}_{user_index}"
                )
                self.bots.append(bot)
                new_bots.append(bot)
                print(f"Created additional bot {bot.bot_id} for user {user_index} -> {meeting_url}")
        
        print(f"Successfully created {len(new_bots)} additional bots. Total bots: {len(self.bots)}")
        return new_bots
    
    def start_all_bots(self, language: str = 'en', task: str = 'transcribe', max_workers: int = 5, 
                      distribution_seconds: float = 0.0) -> List[Dict[str, Any]]:
        """
        Start all bots by calling create() on each one using threading with random time distribution.
        
        Args:
            language: Language code for transcription
            task: Transcription task
            max_workers: Maximum number of concurrent threads
            distribution_seconds: Random delay range in seconds (0.0 = no delay, 5.0 = 0-5s random delay)
            
        Returns:
            List of meeting info dictionaries from bot creation
        """
        if not self.bots:
            raise Exception("No bots created. Call create_bots() first.")
        
        if distribution_seconds > 0:
            print(f"Starting {len(self.bots)} bots using {max_workers} threads with {distribution_seconds}s random distribution...")
        else:
            print(f"Starting {len(self.bots)} bots using {max_workers} threads...")
        
        results = []
        
        def start_bot_with_delay(bot):
            try:
                # Add random delay if distribution_seconds > 0
                if distribution_seconds > 0:
                    delay = random.uniform(0, distribution_seconds)
                    time.sleep(delay)
                    print(f"Started bot {bot.bot_id} (after {delay:.2f}s delay)")
                else:
                    print(f"Started bot {bot.bot_id}")
                
                meeting_info = bot.create(language=language, task=task)
                return {'bot_id': bot.bot_id, 'result': meeting_info}
            except Exception as e:
                print(f"Failed to start bot {bot.bot_id}: {e}")
                return {'bot_id': bot.bot_id, 'error': str(e)}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all bot start tasks
            future_to_bot = {executor.submit(start_bot_with_delay, bot): bot for bot in self.bots}
            
            # Collect results as they complete
            for future in as_completed(future_to_bot):
                result = future.result()
                results.append(result.get('result', result.get('error')))
        
        print(f"Successfully started {len([r for r in results if 'error' not in r])} bots")
        return results
    
    def start_new_bots(self, new_bots: List[Bot], language: str = 'en', task: str = 'transcribe', max_workers: int = 5,
                      distribution_seconds: float = 0.0) -> List[Dict[str, Any]]:
        """
        Start only the newly created bots using threading with random time distribution.
        
        Args:
            new_bots: List of newly created Bot instances
            language: Language code for transcription
            task: Transcription task
            max_workers: Maximum number of concurrent threads
            distribution_seconds: Random delay range in seconds (0.0 = no delay, 5.0 = 0-5s random delay)
            
        Returns:
            List of meeting info dictionaries from bot creation
        """
        if not new_bots:
            print("No new bots to start")
            return []
        
        if distribution_seconds > 0:
            print(f"Starting {len(new_bots)} new bots using {max_workers} threads with {distribution_seconds}s random distribution...")
        else:
            print(f"Starting {len(new_bots)} new bots using {max_workers} threads...")
        
        results = []
        
        def start_bot_with_delay(bot):
            try:
                # Add random delay if distribution_seconds > 0
                if distribution_seconds > 0:
                    delay = random.uniform(0, distribution_seconds)
                    time.sleep(delay)
                    print(f"Started new bot {bot.bot_id} (after {delay:.2f}s delay)")
                else:
                    print(f"Started new bot {bot.bot_id}")
                
                meeting_info = bot.create(language=language, task=task)
                return {'bot_id': bot.bot_id, 'result': meeting_info}
            except Exception as e:
                print(f"Failed to start new bot {bot.bot_id}: {e}")
                return {'bot_id': bot.bot_id, 'error': str(e)}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all bot start tasks
            future_to_bot = {executor.submit(start_bot_with_delay, bot): bot for bot in new_bots}
            
            # Collect results as they complete
            for future in as_completed(future_to_bot):
                result = future.result()
                results.append(result.get('result', result.get('error')))
        
        print(f"Successfully started {len([r for r in results if 'error' not in r])} new bots")
        return results
    
    def scale_to_users(self, target_users: int, meeting_urls: List[str], bot_name_prefix: str = "TestBot") -> Dict[str, Any]:
        """
        Scale the test suite to a target number of users, creating additional users and bots as needed.
        
        Args:
            target_users: Target total number of users
            meeting_urls: List of meeting URLs to distribute among users
            bot_name_prefix: Prefix for bot names
            
        Returns:
            Dictionary with scaling results and statistics
        """
        if target_users <= 0:
            raise ValueError("target_users must be greater than 0")
        
        current_users = len(self.users)
        
        if target_users < current_users:
            print(f"Warning: Target users ({target_users}) is less than current users ({current_users})")
            print("This method only adds users/bots, it doesn't remove them")
            return {
                'users_added': 0,
                'bots_added': 0,
                'total_users': current_users,
                'total_bots': len(self.bots),
                'action': 'no_change',
                'warning': f'Target ({target_users}) < current ({current_users})'
            }
        
        # Check if we need to add users
        users_to_add = max(0, target_users - current_users)
        
        # Check if we need to add bots (even if user count matches)
        current_bots = len(self.bots)
        bots_needed = target_users  # Each user should have one bot
        
        if users_to_add == 0 and current_bots >= bots_needed:
            print(f"Already at target of {target_users} users with {current_bots} bots")
            return {
                'users_added': 0,
                'bots_added': 0,
                'total_users': current_users,
                'total_bots': current_bots,
                'action': 'no_change'
            }
        
        print(f"Scaling from {current_users} users to {target_users} users (+{users_to_add})")
        print(f"Current bots: {current_bots}, Target bots: {bots_needed}")
        
        # Add users if needed
        new_users = []
        if users_to_add > 0:
            new_users = self.add_users(users_to_add)
        
        # Add bots for users that don't have them
        new_bots = self.add_bots(meeting_urls, bot_name_prefix)
        
        return {
            'users_added': len(new_users),
            'bots_added': len(new_bots),
            'total_users': len(self.users),
            'total_bots': len(self.bots),
            'action': 'scaled_up' if (len(new_users) > 0 or len(new_bots) > 0) else 'no_change',
            'new_users': new_users,
            'new_bots': new_bots
        }
    
    def stop_all_bots(self, max_workers: int = 5) -> List[Dict[str, str]]:
        """
        Stop all running bots using threading.
        
        Args:
            max_workers: Maximum number of concurrent threads
        
        Returns:
            List of stop confirmation messages
        """
        if not self.bots:
            raise Exception("No bots created.")
        
        print(f"Stopping {len(self.bots)} bots using {max_workers} threads...")
        results = []
        
        def stop_bot(bot):
            try:
                if bot.created:
                    result = bot.stop()
                    print(f"Stopped bot {bot.bot_id}")
                    return {'bot_id': bot.bot_id, 'result': result}
                else:
                    print(f"Bot {bot.bot_id} was not running")
                    return {'bot_id': bot.bot_id, 'result': {'message': 'Bot was not running'}}
            except Exception as e:
                print(f"Failed to stop bot {bot.bot_id}: {e}")
                return {'bot_id': bot.bot_id, 'error': str(e)}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all bot stop tasks
            future_to_bot = {executor.submit(stop_bot, bot): bot for bot in self.bots}
            
            # Collect results as they complete
            for future in as_completed(future_to_bot):
                result = future.result()
                results.append(result.get('result', result.get('error')))
        
        return results
    
    # Monitoring/polling removed; snapshots are computed on demand
    
    def snapshot(self, max_workers: int = 5) -> Dict[str, Any]:
        """
        Take a snapshot of current bot states using threading for API calls.
        
        Args:
            max_workers: Maximum number of concurrent threads for API calls
            
        Returns:
            Dictionary with current bot states and metadata
        """
        snapshot_data = {
            'timestamp': time.time(),
            'datetime': datetime.now().isoformat(),
            'bots': []
        }
        
        def get_bot_snapshot(bot):
            """Get snapshot data for a single bot."""
            try:
                bot_stats = bot.get_stats()
                
                # Get current transcript if bot is created
                transcript_data = None
                status_transitions = None
                
                if bot.created:
                    try:
                        transcript = bot.get_transcript()
                        segments = transcript.get('segments', [])
                        # Compute first/last segment absolute times using provided absolute timestamps only
                        first_segment_time = None
                        last_segment_start_time = None
                        last_segment_end_time = None
                        if segments:
                            first_segment_time = segments[0].get('absolute_start_time')
                            last_segment_start_time = segments[-1].get('absolute_start_time')
                            last_segment_end_time = segments[-1].get('absolute_end_time')
                        transcript_data = {
                            'segments': segments,
                            'segments_count': len(segments),
                            'has_transcript': len(segments) > 0,
                            'first_segment_time': first_segment_time,
                            'last_segment_time': last_segment_start_time,
                            'last_segment_end_time': last_segment_end_time
                        }
                    except Exception as e:
                        transcript_data = {'error': str(e)}
                    
                    # Get status transitions from meeting data
                    try:
                        meeting_status = bot.get_meeting_status()
                        if meeting_status and 'data' in meeting_status:
                            status_transitions = meeting_status['data'].get('status_transition', [])
                    except Exception as e:
                        status_transitions = {'error': str(e)}
                
                return {
                    **bot_stats,
                    'transcript': transcript_data,
                    'status_transitions': status_transitions
                }
                
            except Exception as e:
                return {
                    'bot_id': bot.bot_id,
                    'error': str(e)
                }
        
        # Use threading to get bot snapshots concurrently
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all bot snapshot tasks
            future_to_bot = {executor.submit(get_bot_snapshot, bot): bot for bot in self.bots}
            
            # Collect results as they complete
            for future in as_completed(future_to_bot):
                bot_snapshot = future.result()
                snapshot_data['bots'].append(bot_snapshot)
        
        return snapshot_data
    
    def parse_for_pandas(self, snapshot_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Parse snapshot data for pandas DataFrame creation.
        
        Args:
            snapshot_data: Optional snapshot data (uses latest if not provided)
            
        Returns:
            List of dictionaries suitable for pandas DataFrame
        """
        if snapshot_data is None:
            # Compute a fresh snapshot if none provided
            snapshot_data = self.snapshot()
        
        rows = []
        for bot_data in snapshot_data['bots']:
            if 'error' in bot_data:
                continue
                
            row = {
                'timestamp': snapshot_data['timestamp'],
                'datetime': snapshot_data['datetime'],
                'bot_id': bot_data['bot_id'],
                'meeting_url': bot_data['meeting_url'],
                'platform': bot_data['platform'],
                'native_meeting_id': bot_data['native_meeting_id'],
                'created': bot_data['created'],
                'meeting_status': bot_data.get('meeting_status'),
                'created_at': bot_data.get('created_at'),
                'end_time': bot_data.get('end_time'),
                'first_transcript_time': bot_data.get('first_transcript_time'),
                'last_transcript_time': bot_data.get('last_transcript_time'),
            }
            
            # Add transcript data if available
            if bot_data.get('transcript'):
                transcript = bot_data['transcript']
                
                # Extract languages from segments
                languages = set()
                segments = transcript.get('segments', [])
                for segment in segments:
                    if 'language' in segment:
                        languages.add(segment['language'])
                
                row.update({
                    'segments_count': len(segments),
                    'has_transcript': len(segments) > 0,
                    'first_segment_time': transcript.get('first_segment_time'),
                    'last_segment_time': transcript.get('last_segment_time'),
                    'last_segment_end_time': transcript.get('last_segment_end_time'),
                    'transcript_error': transcript.get('error'),
                    'detected_languages': list(languages) if languages else [],
                    'languages_count': len(languages)
                })
                
            # Compute baseline t0 and status transition durations
            # t0 preference: created_at if present else first transition timestamp
            transitions = bot_data.get('status_transitions') or []
            t0 = None
            try:
                import pandas as pd
                if bot_data.get('created_at'):
                    t0 = pd.to_datetime(bot_data['created_at'])
                elif transitions:
                    first_ts = transitions[0].get('timestamp')
                    t0 = pd.to_datetime(first_ts) if first_ts else None
            except Exception:
                t0 = None
            row['t0'] = t0.isoformat() if t0 is not None else None
            
            # Determine milestone timestamps
            joining_ts = None
            awaiting_admission_ts = None
            active_ts = None
            requested_ts = None
            try:
                for tr in transitions:
                    to_state = tr.get('to')
                    ts = tr.get('timestamp')
                    ts_dt = pd.to_datetime(ts) if ts else None
                    if to_state == 'joining' and joining_ts is None:
                        joining_ts = ts_dt
                        # If the first transition is from requested, infer requested at created_at
                        if tr.get('from') == 'requested' and bot_data.get('created_at'):
                            requested_ts = pd.to_datetime(bot_data['created_at'])
                    elif to_state == 'awaiting_admission' and awaiting_admission_ts is None:
                        awaiting_admission_ts = ts_dt
                    elif to_state == 'active' and active_ts is None:
                        active_ts = ts_dt
            except Exception:
                pass
            
            # Compute durations in seconds
            def diff_seconds(a, b):
                try:
                    if a is None or b is None:
                        return None
                    return (b - a).total_seconds()
                except Exception:
                    return None
            
            row['time_0_to_requested'] = diff_seconds(t0, requested_ts) if requested_ts is not None else (0.0 if t0 is not None and requested_ts is None and transitions and transitions[0].get('from') == 'requested' else None)
            row['time_requested_to_joining'] = diff_seconds(requested_ts, joining_ts)
            row['time_joining_to_awaiting_admission'] = diff_seconds(joining_ts, awaiting_admission_ts)
            row['time_awaiting_admission_to_active'] = diff_seconds(awaiting_admission_ts, active_ts)
            
            # Current/last status
            if transitions:
                row['current_status'] = transitions[-1].get('to')
                row['initial_status'] = transitions[0].get('from')
                row['last_transition_time'] = transitions[-1].get('timestamp')
            else:
                row['current_status'] = bot_data.get('meeting_status')
                row['initial_status'] = None
                row['last_transition_time'] = None
            row['status_transitions'] = transitions if transitions else None
            row['status_transitions_count'] = len(transitions) if transitions else 0
            row['completion_reason'] = transitions[-1].get('completion_reason') if transitions else None
            
            # Active to first transcript latency
            first_segment_time = row.get('first_segment_time')
            if active_ts is not None and first_segment_time:
                try:
                    first_dt = pd.to_datetime(first_segment_time)
                    # Make active_ts timezone-aware to match first_dt
                    if active_ts.tz is None and first_dt.tz is not None:
                        active_ts = active_ts.tz_localize('UTC')
                    elif active_ts.tz is not None and first_dt.tz is None:
                        first_dt = first_dt.tz_localize('UTC')
                    row['active_to_first_transcript'] = (first_dt - active_ts).total_seconds()
                except Exception:
                    row['active_to_first_transcript'] = None
            else:
                row['active_to_first_transcript'] = None
            
            # Transcription latency: last segment end minus time when transcript was requested
            # Use created_at as the time when transcript was requested
            last_segment_end_time = row.get('last_segment_end_time')
            created_at = bot_data.get('created_at')
            if created_at and last_segment_end_time:
                try:
                    created_dt = pd.to_datetime(created_at)
                    last_end_dt = pd.to_datetime(last_segment_end_time)
                    # Make timezones consistent
                    if created_dt.tz is None and last_end_dt.tz is not None:
                        created_dt = created_dt.tz_localize('UTC')
                    elif created_dt.tz is not None and last_end_dt.tz is None:
                        last_end_dt = last_end_dt.tz_localize('UTC')
                    row['transcription_latency'] = (pd.Timestamp.now(tz='UTC') - last_end_dt).total_seconds()
                except Exception:
                    row['transcription_latency'] = None
            else:
                row['transcription_latency'] = None
            
            rows.append(row)
        
        return rows
    
    def get_latest_dataframe(self, max_workers: int = 5) -> pd.DataFrame:
        """
        Get the latest monitoring data as a pandas DataFrame.
        
        Args:
            max_workers: Maximum number of concurrent threads for API calls
            
        Returns:
            DataFrame with latest bot states
        """
        # Compute a fresh snapshot on demand
        snapshot = self.snapshot(max_workers=max_workers)
        rows = self.parse_for_pandas(snapshot)
        return pd.DataFrame(rows)
    
    def cleanup(self) -> None:
        """Clean up all resources (stop monitoring, stop bots, etc.)."""
        print("Cleaning up TestSuite...")
        
        # Stop all bots
        if self.bots:
            self.stop_all_bots()
        
        print("TestSuite cleanup completed")
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the current test suite state.
        
        Returns:
            Dictionary with summary statistics
        """
        summary = {
            'total_users': len(self.users),
            'total_bots': len(self.bots),
            'created_bots': len([b for b in self.bots if b.created]),
            'user_meeting_mapping': self.user_meeting_mapping
        }
        # Calculate a quick snapshot-based metric
        try:
            snap = self.snapshot()
            summary['latest_snapshot_time'] = snap.get('datetime')
            summary['bots_with_transcripts'] = len([
                b for b in snap.get('bots', [])
                if b.get('transcript', {}).get('has_transcript', False)
            ])
        except Exception:
            pass
        return summary
    
    def format_status_transitions(self, transitions: List[Dict[str, Any]]) -> str:
        """
        Format status transitions for nice display.
        
        Args:
            transitions: List of status transition dictionaries
            
        Returns:
            Formatted string showing status flow
        """
        if not transitions:
            return "No transitions"
        
        if isinstance(transitions, dict) and 'error' in transitions:
            return f"Error: {transitions['error']}"
        
        # Create a flow representation
        flow_parts = []
        for i, transition in enumerate(transitions):
            from_status = transition.get('from', 'unknown')
            to_status = transition.get('to', 'unknown')
            timestamp = transition.get('timestamp', '')
            source = transition.get('source', '')
            
            # Format timestamp (show only time part)
            if timestamp:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    time_str = dt.strftime('%H:%M:%S')
                except:
                    time_str = timestamp[-8:] if len(timestamp) > 8 else timestamp
            else:
                time_str = ''
            
            # Create transition arrow
            arrow = f"{from_status} → {to_status}"
            if time_str:
                arrow += f" ({time_str})"
            if source:
                arrow += f" [{source}]"
            
            flow_parts.append(arrow)
        
        return " | ".join(flow_parts)
    
    def format_languages(self, languages: List[str]) -> str:
        """
        Format detected languages for nice display.
        
        Args:
            languages: List of language codes
            
        Returns:
            Formatted string showing languages
        """
        if not languages:
            return "No languages detected"
        
        # Convert language codes to readable names if needed
        lang_names = {
            'en': 'English',
            'es': 'Spanish', 
            'fr': 'French',
            'de': 'German',
            'it': 'Italian',
            'pt': 'Portuguese',
            'ru': 'Russian',
            'zh': 'Chinese',
            'ja': 'Japanese',
            'ko': 'Korean',
            'ar': 'Arabic',
            'hi': 'Hindi'
        }
        
        formatted_langs = []
        for lang in sorted(languages):
            display_name = lang_names.get(lang.lower(), lang.upper())
            formatted_langs.append(display_name)
        
        return ", ".join(formatted_langs)
    
    def get_status_summary_dataframe(self, max_workers: int = 5) -> pd.DataFrame:
        """
        Get a DataFrame focused on status transitions and bot states.
        
        Args:
            max_workers: Maximum number of concurrent threads for API calls
            
        Returns:
            DataFrame with status-focused columns
        """
        df = self.get_latest_dataframe(max_workers=max_workers)
        
        if df.empty:
            return df
        
        # Add formatted status transitions
        df['status_flow'] = df['status_transitions'].apply(
            lambda x: self.format_status_transitions(x) if pd.notna(x) else "No data"
        )
        
        # Add formatted languages
        df['languages_formatted'] = df['detected_languages'].apply(
            lambda x: self.format_languages(x) if pd.notna(x) and (isinstance(x, list) and len(x) > 0) else "No languages detected"
        )
        
        # Add latency column: last_transition_time - t0
        df['latency'] = pd.to_datetime(df['last_transition_time']) - pd.to_datetime(df['t0'])
        
        # Convert all timestamp columns to datetime
        timestamp_cols = ['t0', 'created_at', 'end_time', 'first_transcript_time', 'last_transcript_time', 
                         'first_segment_time', 'last_segment_time', 'last_segment_end_time', 'last_transition_time']
        for col in timestamp_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        
        # Select relevant columns for status monitoring
        status_cols = [
            'bot_id', 'platform', 'meeting_status', 'current_status', 'created',
            'segments_count', 'detected_languages', 'languages_count', 'languages_formatted',
            'transcription_latency', 'active_to_first_transcript', 'latency',
            'time_0_to_requested', 'time_requested_to_joining',
            'time_joining_to_awaiting_admission', 'time_awaiting_admission_to_active',
            'last_segment_time', 'last_segment_end_time', 'last_transition_time',
            'status_transitions_count', 'completion_reason', 'status_flow'
        ]
        
        # Only include columns that exist
        available_cols = [col for col in status_cols if col in df.columns]
        
        return df[available_cols]
