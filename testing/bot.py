"""
Bot class for managing individual Vexa bots.

This class represents a single bot instance with methods for:
- Creating bots (request_bot)
- Getting transcripts
- Monitoring bot status
"""

import time
import random
from typing import Optional, Dict, Any, List
from vexa_client import VexaClient
from vexa_client.vexa import parse_url


class Bot:
    """
    Represents a single Vexa bot instance.
    
    Each bot is associated with a specific user client and meeting.
    """
    
    def __init__(self, user_client: VexaClient, meeting_url: str, bot_id: Optional[str] = None):
        """
        Initialize a Bot instance.
        
        Args:
            user_client: VexaClient instance for the user
            meeting_url: Full meeting URL (e.g., "https://teams.live.com/meet/9398850880426?p=RBZCWdxyp85TpcKna8")
            bot_id: Optional unique identifier for this bot instance
        """
        self.user_client = user_client
        self.meeting_url = meeting_url
        self.bot_id = bot_id or f"bot_{random.randint(1000, 9999)}"
        
        # Parse meeting URL to extract platform and meeting ID
        self.platform, self.native_meeting_id, self.passcode = parse_url(meeting_url)
        
        # Bot state tracking
        self.created = False
        self.meeting_info = None
        self.last_transcript_time = None
        self.first_transcript_time = None
        
    def create(self, bot_name: Optional[str] = None, language: str = 'en', task: str = 'transcribe') -> Dict[str, Any]:
        """
        Create/request a bot for this meeting.
        
        Args:
            bot_name: Optional name for the bot in the meeting
            language: Language code for transcription (default: 'en')
            task: Transcription task ('transcribe' or 'translate', default: 'transcribe')
            
        Returns:
            Dictionary representing the created/updated Meeting object
        """
        try:
            self.meeting_info = self.user_client.request_bot(
                platform=self.platform,
                native_meeting_id=self.native_meeting_id,
                bot_name=bot_name or f"Vexa-{self.bot_id}",
                language=language,
                task=task,
                passcode=self.passcode
            )
            self.created = True
            return self.meeting_info
        except Exception as e:
            raise Exception(f"Failed to create bot {self.bot_id}: {e}")
    
    def get_transcript(self) -> Dict[str, Any]:
        """
        Get the current transcript for this bot's meeting.
        
        Returns:
            Dictionary containing meeting details and transcript segments
        """
        if not self.created:
            raise Exception(f"Bot {self.bot_id} has not been created yet. Call create() first.")
        
        try:
            transcript = self.user_client.get_transcript(
                platform=self.platform,
                native_meeting_id=self.native_meeting_id
            )
            
            # Track transcript timing
            if transcript.get('segments'):
                current_time = time.time()
                if self.first_transcript_time is None:
                    self.first_transcript_time = current_time
                self.last_transcript_time = current_time
            
            return transcript
        except Exception as e:
            raise Exception(f"Failed to get transcript for bot {self.bot_id}: {e}")
    
    def get_meeting_status(self) -> Optional[Dict[str, Any]]:
        """
        Get the current status of this bot's meeting.
        
        Returns:
            Dictionary representing the Meeting object, or None if not found
        """
        if not self.created:
            return None
        
        try:
            return self.user_client.get_meeting_by_id(
                platform=self.platform,
                native_meeting_id=self.native_meeting_id
            )
        except Exception as e:
            print(f"Warning: Could not get meeting status for bot {self.bot_id}: {e}")
            return None
    
    def stop(self) -> Dict[str, str]:
        """
        Stop this bot.
        
        Returns:
            Dictionary containing a confirmation message
        """
        if not self.created:
            raise Exception(f"Bot {self.bot_id} has not been created yet.")
        
        try:
            result = self.user_client.stop_bot(
                platform=self.platform,
                native_meeting_id=self.native_meeting_id
            )
            self.created = False
            return result
        except Exception as e:
            raise Exception(f"Failed to stop bot {self.bot_id}: {e}")
    
    def update_config(self, language: Optional[str] = None, task: Optional[str] = None) -> Dict[str, Any]:
        """
        Update bot configuration (language, task).
        
        Args:
            language: Optional new language code
            task: Optional new task ('transcribe' or 'translate')
            
        Returns:
            Dictionary containing a confirmation message
        """
        if not self.created:
            raise Exception(f"Bot {self.bot_id} has not been created yet.")
        
        try:
            return self.user_client.update_bot_config(
                platform=self.platform,
                native_meeting_id=self.native_meeting_id,
                language=language,
                task=task
            )
        except Exception as e:
            raise Exception(f"Failed to update config for bot {self.bot_id}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about this bot's performance.
        
        Returns:
            Dictionary with bot statistics
        """
        stats = {
            'bot_id': self.bot_id,
            'meeting_url': self.meeting_url,
            'platform': self.platform,
            'native_meeting_id': self.native_meeting_id,
            'created': self.created,
            'first_transcript_time': self.first_transcript_time,
            'last_transcript_time': self.last_transcript_time,
        }
        
        if self.created:
            meeting_status = self.get_meeting_status()
            if meeting_status:
                stats.update({
                    'meeting_status': meeting_status.get('status'),
                    'start_time': meeting_status.get('start_time'),
                    'end_time': meeting_status.get('end_time'),
                })
        
        return stats
