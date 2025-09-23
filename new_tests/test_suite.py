"""
TestSuite class for managing multiple users and bots in Vexa testing scenarios.

This class provides:
- User creation and management
- Random user-meeting mapping
- Bot lifecycle management
- Background monitoring capabilities
- Snapshot and pandas integration for notebook use
"""

import time
import random
import threading
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import pandas as pd

from vexa_client import VexaClient
from bot import Bot


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
                 poll_interval: float = 2.0):
        """
        Initialize the TestSuite.
        
        Args:
            base_url: Base URL for the Vexa API
            admin_api_key: Admin API key for user creation
            poll_interval: Interval between monitoring polls (seconds)
        """
        self.base_url = base_url
        self.admin_api_key = admin_api_key
        self.poll_interval = poll_interval
        
        # Initialize admin client if API key provided
        self.admin_client = None
        if admin_api_key:
            self.admin_client = VexaClient(
                base_url=base_url,
                admin_key=admin_api_key
            )
        
        # Test suite state
        self.users: List[VexaClient] = []
        self.bots: List[Bot] = []
        self.user_meeting_mapping: Dict[int, str] = {}  # user_index -> meeting_url
        self.monitoring = False
        self.monitor_thread = None
        self.polls: List[Dict[str, Any]] = []  # Store all poll data with timestamps
        
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
                user_client = VexaClient(
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
    
    def start_all_bots(self, language: str = 'en', task: str = 'transcribe') -> List[Dict[str, Any]]:
        """
        Start all bots by calling create() on each one.
        
        Args:
            language: Language code for transcription
            task: Transcription task
            
        Returns:
            List of meeting info dictionaries from bot creation
        """
        if not self.bots:
            raise Exception("No bots created. Call create_bots() first.")
        
        print(f"Starting {len(self.bots)} bots...")
        results = []
        
        for bot in self.bots:
            try:
                meeting_info = bot.create(language=language, task=task)
                results.append(meeting_info)
                print(f"Started bot {bot.bot_id}")
            except Exception as e:
                print(f"Failed to start bot {bot.bot_id}: {e}")
                results.append({'error': str(e)})
        
        print(f"Successfully started {len([r for r in results if 'error' not in r])} bots")
        return results
    
    def stop_all_bots(self) -> List[Dict[str, str]]:
        """
        Stop all running bots.
        
        Returns:
            List of stop confirmation messages
        """
        if not self.bots:
            raise Exception("No bots created.")
        
        print(f"Stopping {len(self.bots)} bots...")
        results = []
        
        for bot in self.bots:
            try:
                if bot.created:
                    result = bot.stop()
                    results.append(result)
                    print(f"Stopped bot {bot.bot_id}")
                else:
                    print(f"Bot {bot.bot_id} was not running")
                    results.append({'message': 'Bot was not running'})
            except Exception as e:
                print(f"Failed to stop bot {bot.bot_id}: {e}")
                results.append({'error': str(e)})
        
        return results
    
    def start_monitoring(self) -> None:
        """
        Start background monitoring of all bots.
        Records actual timestamps in polls[] for latency calculations.
        """
        if self.monitoring:
            print("Monitoring already running")
            return
        
        print("Starting background monitoring...")
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        print("Background monitoring started")
    
    def stop_monitoring(self) -> None:
        """Stop background monitoring."""
        if not self.monitoring:
            print("Monitoring not running")
            return
        
        print("Stopping background monitoring...")
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        print("Background monitoring stopped")
    
    def _monitor_loop(self) -> None:
        """Internal monitoring loop that runs in background thread."""
        while self.monitoring:
            try:
                poll_data = self.snapshot()
                self.polls.append(poll_data)
                time.sleep(self.poll_interval)
            except Exception as e:
                print(f"Error in monitoring loop: {e}")
                time.sleep(self.poll_interval)
    
    def snapshot(self) -> Dict[str, Any]:
        """
        Take a snapshot of current bot states.
        
        Returns:
            Dictionary with current bot states and metadata
        """
        snapshot_data = {
            'timestamp': time.time(),
            'datetime': datetime.now().isoformat(),
            'bots': []
        }
        
        for bot in self.bots:
            try:
                bot_stats = bot.get_stats()
                
                # Get current transcript if bot is created
                transcript_data = None
                status_transitions = None
                
                if bot.created:
                    try:
                        transcript = bot.get_transcript()
                        transcript_data = {
                            'segments': transcript.get('segments', []),
                            'segments_count': len(transcript.get('segments', [])),
                            'has_transcript': len(transcript.get('segments', [])) > 0,
                            'last_segment_time': transcript.get('segments', [{}])[-1].get('absolute_start_time') if transcript.get('segments') else None
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
                
                bot_snapshot = {
                    **bot_stats,
                    'transcript': transcript_data,
                    'status_transitions': status_transitions
                }
                snapshot_data['bots'].append(bot_snapshot)
                
            except Exception as e:
                snapshot_data['bots'].append({
                    'bot_id': bot.bot_id,
                    'error': str(e)
                })
        
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
            if not self.polls:
                return []
            snapshot_data = self.polls[-1]
        
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
                    'last_segment_time': segments[-1].get('absolute_start_time') if segments else None,
                    'transcript_error': transcript.get('error'),
                    'detected_languages': list(languages) if languages else [],
                    'languages_count': len(languages)
                })
                
                # Calculate transcription latency if we have segments
                if segments and segments[-1].get('absolute_start_time'):
                    try:
                        from datetime import datetime
                        import pandas as pd
                        
                        # Parse the last segment time and duration
                        last_segment = segments[-1]
                        last_segment_time = last_segment['absolute_start_time']
                        segment_start_dt = pd.to_datetime(last_segment_time)
                        
                        # Calculate segment end time (start + duration)
                        segment_duration = last_segment.get('end_time', 0) - last_segment.get('start_time', 0)
                        segment_end_dt = segment_start_dt + pd.Timedelta(seconds=segment_duration)
                        
                        # Calculate latency from current time to segment completion
                        current_time = pd.Timestamp.now(tz='UTC')
                        latency_seconds = (current_time - segment_end_dt).total_seconds()
                        
                        row['transcription_latency'] = latency_seconds
                    except Exception as e:
                        row['transcription_latency'] = None
                else:
                    row['transcription_latency'] = None
            
            # Add status transition data if available
            if bot_data.get('status_transitions'):
                transitions = bot_data['status_transitions']
                row.update({
                    'status_transitions_count': len(transitions),
                    'current_status': transitions[-1]['to'] if transitions else None,
                    'initial_status': transitions[0]['from'] if transitions else None,
                    'last_transition_time': transitions[-1]['timestamp'] if transitions else None,
                    'completion_reason': transitions[-1].get('completion_reason') if transitions else None,
                    'status_transitions': transitions  # Keep full data for detailed analysis
                })
            
            rows.append(row)
        
        return rows
    
    def get_latest_dataframe(self) -> pd.DataFrame:
        """
        Get the latest monitoring data as a pandas DataFrame.
        
        Returns:
            DataFrame with latest bot states
        """
        if not self.polls:
            return pd.DataFrame()
        
        rows = self.parse_for_pandas()
        return pd.DataFrame(rows)
    
    def get_all_dataframe(self) -> pd.DataFrame:
        """
        Get all monitoring data as a pandas DataFrame.
        
        Returns:
            DataFrame with all historical bot states
        """
        all_rows = []
        for poll in self.polls:
            rows = self.parse_for_pandas(poll)
            all_rows.extend(rows)
        
        return pd.DataFrame(all_rows)
    
    def cleanup(self) -> None:
        """Clean up all resources (stop monitoring, stop bots, etc.)."""
        print("Cleaning up TestSuite...")
        
        # Stop monitoring
        self.stop_monitoring()
        
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
            'monitoring_active': self.monitoring,
            'total_polls': len(self.polls),
            'user_meeting_mapping': self.user_meeting_mapping
        }
        
        if self.polls:
            latest_poll = self.polls[-1]
            summary['latest_poll_time'] = latest_poll['datetime']
            summary['bots_with_transcripts'] = len([
                b for b in latest_poll['bots'] 
                if b.get('transcript', {}).get('has_transcript', False)
            ])
        
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
    
    def get_status_summary_dataframe(self) -> pd.DataFrame:
        """
        Get a DataFrame focused on status transitions and bot states.
        
        Returns:
            DataFrame with status-focused columns
        """
        df = self.get_latest_dataframe()
        
        if df.empty:
            return df
        
        # Add formatted status transitions
        df['status_flow'] = df['status_transitions'].apply(
            lambda x: self.format_status_transitions(x) if pd.notna(x) else "No data"
        )
        
        # Add formatted languages
        df['languages_formatted'] = df['detected_languages'].apply(
            lambda x: self.format_languages(x) if pd.notna(x) and x else "No languages detected"
        )
        
        # Select relevant columns for status monitoring
        status_cols = [
            'bot_id', 'platform', 'created', 'current_status', 'initial_status',
            'status_transitions_count', 'completion_reason', 'status_flow',
            'segments_count', 'has_transcript', 'last_transition_time',
            'detected_languages', 'languages_count', 'languages_formatted',
            'transcription_latency', 'last_segment_time'
        ]
        
        # Only include columns that exist
        available_cols = [col for col in status_cols if col in df.columns]
        
        return df[available_cols]
