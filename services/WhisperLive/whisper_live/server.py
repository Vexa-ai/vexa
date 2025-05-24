import os
import time
import threading
import json
import functools
import logging
from enum import Enum
from typing import List, Optional, Tuple, Dict, Any
import datetime
from datetime import timedelta
import websocket

import torch
import numpy as np
from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed
from whisper_live.vad import VoiceActivityDetector
from whisper_live.transcriber import WhisperModel
try:
    from whisper_live.transcriber_tensorrt import WhisperTRTLLM
except Exception:
    pass

# Import for health check HTTP server
import http.server
import socketserver
import threading

# Import Redis
import redis
import uuid

# Pydantic and dataclasses for speaker matching
from pydantic import BaseModel
from dataclasses import dataclass, field
from datetime import datetime as dt
from datetime import timezone

# Setup basic logging
logging.basicConfig(level=logging.INFO)

# Add file logging for transcription data
LOG_DIR = "transcription_logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_filename = os.path.join(LOG_DIR, f"transcription_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
file_handler = logging.FileHandler(log_filename)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger = logging.getLogger("transcription")
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)

# Use the existing logger instance from the file for speaker matching related logs
speaker_logger = logging.getLogger("transcription")

class SpeakerMeta(BaseModel):
    """Speaker metadata with activity information."""
    name: str
    mic_level: float
    timestamp: dt
    delay_sec: float = 0.0
    meeting_id: Optional[str] = None
    user_id: Optional[str] = None
    meta_bits: Optional[str] = None

    @classmethod
    def from_client_payload(cls, speaker_payload: Dict[str, Any], meeting_id: str, timestamp_override: dt) -> 'SpeakerMeta':
        """Create SpeakerMeta from client's speaker_activity_update payload item.
        
        Args:
            speaker_payload: Dict containing 'id', 'name', 'mic_activity_bits'.
            meeting_id: The meeting ID for this session.
            timestamp_override: The timestamp from the parent speaker_activity_update message.
                
        Returns:
            SpeakerMeta object with parsed data
        """
        try:
            meta = speaker_payload.get('mic_activity_bits', '')
            mic_level = sum(1 for c in meta if c == '1') / max(len(meta), 1) if meta else 0.0
            
            return cls(
                name=speaker_payload['name'],
                mic_level=mic_level,
                timestamp=timestamp_override,
                user_id=speaker_payload['id'],
                meeting_id=meeting_id,
                meta_bits=meta
            )
        except KeyError as e:
            speaker_logger.error(f"Missing key {e} in speaker_payload: {speaker_payload}")
            raise ValueError(f"Invalid speaker data format, missing key: {e}")
        except Exception as e:
            speaker_logger.error(f"Error parsing speaker data from client payload: {e}", exc_info=True)
            raise ValueError(f"Invalid speaker data format: {e}")

def convert_speaker_activity_to_meta(activity_payload: Dict[str, Any]) -> List[SpeakerMeta]:
    """Convert 'speaker_activity_update' payload to a list of SpeakerMeta objects.
    
    Args:
        activity_payload: The full payload of a 'speaker_activity_update' message.
                          Expected to have 'speakers' list, 'meeting_id', and 'timestamp'.
        
    Returns:
        List of SpeakerMeta objects.
    """
    speaker_metas = []
    common_meeting_id = activity_payload.get('meeting_id')
    common_timestamp_str = activity_payload.get('timestamp')

    if not common_timestamp_str:
        speaker_logger.warning("No 'timestamp' in speaker_activity_update payload. Skipping.")
        return []
    
    try:
        common_timestamp = dt.fromisoformat(common_timestamp_str.replace('Z', '+00:00'))
        if common_timestamp.tzinfo is None:
            common_timestamp = common_timestamp.replace(tzinfo=timezone.utc)
    except ValueError:
        speaker_logger.error(f"Invalid ISO format for common_timestamp: {common_timestamp_str}")
        return []

    client_speakers = activity_payload.get('speakers', [])
    for speaker_data_item in client_speakers:
        try:
            speaker_meta = SpeakerMeta.from_client_payload(speaker_data_item, common_meeting_id, common_timestamp)
            speaker_metas.append(speaker_meta)
        except Exception as e:
            speaker_logger.warning(f"Skipping invalid speaker data item: {speaker_data_item}, error: {e}")
            continue
    return speaker_metas

@dataclass
class TranscriptSegment:
    """A segment of transcribed speech with timing and speaker information."""
    content: str
    start_timestamp: float
    end_timestamp: float
    speaker: Optional[str] = None
    speaker_id: Optional[str] = None
    confidence: float = 0.0
    words: List[Dict[str, Any]] = field(default_factory=list)
    server_timestamp: Optional[dt] = None

    @property
    def duration(self) -> float:
        """Get segment duration in seconds."""
        return self.end_timestamp - self.start_timestamp

    @classmethod
    def from_whisper_live_segment(cls, segment_data: Dict[str, Any], t0_timestamp: Optional[dt] = None) -> 'TranscriptSegment':
        """Create TranscriptSegment from WhisperLive's output segment format.
        
        Args:
            segment_data: Dict from WhisperLive, e.g., 
                          {'text': ' Hello.', 'start': 0.0, 'end': 0.84} or
                          {'text': ' World.', 'start': 0.84, 'end': 1.2, 'words': [{'word': 'World.', 'start': 0.84, 'end': 1.2, 'probability': 0.8}]}
            t0_timestamp: The absolute start time of the audio session (datetime object).
            
        Returns:
            TranscriptSegment object.
        """
        try:
            start_time_sec = float(segment_data["start"])
            end_time_sec = float(segment_data["end"])
            text_content = segment_data["text"].strip()
            
            processed_words = []
            segment_confidence = 0.0

            if "words" in segment_data and segment_data["words"]:
                for word_info in segment_data["words"]:
                    processed_words.append({
                        "word": word_info["word"].strip(),
                        "start": float(word_info["start"]),
                        "end": float(word_info["end"]),
                        "confidence": float(word_info.get("probability", 0.0))
                    })
                word_confidences = [w["confidence"] for w in processed_words if "confidence" in w and w["confidence"] is not None]
                if word_confidences:
                    segment_confidence = sum(word_confidences) / len(word_confidences)
            elif "probability" in segment_data :
                 segment_confidence = float(segment_data.get("probability", 0.0))

            return cls(
                content=text_content,
                start_timestamp=start_time_sec,
                end_timestamp=end_time_sec,
                confidence=segment_confidence,
                words=processed_words,
                server_timestamp=t0_timestamp
            )
            
        except KeyError as e:
            speaker_logger.error(f"Missing key {e} in WhisperLive segment_data: {segment_data}")
            raise ValueError(f"Invalid WhisperLive segment_data format, missing key: {e}")
        except Exception as e:
            speaker_logger.error(f"Error creating TranscriptSegment from WhisperLive segment: {e}", exc_info=True)
            speaker_logger.error(f"Problematic WhisperLive segment data: {segment_data}")
            raise ValueError(f"Invalid WhisperLive segment data format: {e}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert segment to dictionary format for storage or sending."""
        return {
            "text": self.content,
            "start": self.start_timestamp,
            "end": self.end_timestamp,
            "speaker_name": self.speaker,
            "speaker_id": self.speaker_id,
            "confidence": self.confidence,
            "words": self.words,
        }

@dataclass
class SpeakerActivitySegment:
    """A continuous segment of speaker activity."""
    speaker_name: str
    speaker_id: str
    start_time: dt
    end_time: dt
    avg_mic_level: float

    @property
    def duration(self) -> timedelta:
        """Get segment duration."""
        return self.end_time - self.start_time
        
    def intersection_with(self, target_start: dt, target_end: dt) -> timedelta:
        """Calculate temporal intersection with another time range."""
        overlap_start = max(self.start_time, target_start)
        overlap_end = min(self.end_time, target_end)
        if overlap_end > overlap_start:
            return overlap_end - overlap_start
        return timedelta(seconds=0)

class TranscriptSpeakerMatcher:
    """Class for matching transcripts with speakers based on temporal proximity and mic activity."""
    
    def __init__(self, t0: dt, min_mic_level_threshold: float = 0.1, speaker_activity_window_sec: float = 1.0):
        """Initialize the matcher.
        Args:
            t0: Absolute start datetime of the session (UTC).
            min_mic_level_threshold: Minimum mic activity level to consider a speaker active.
            speaker_activity_window_sec: Duration in seconds for which a mic_activity_bit='1' implies speech.
        """
        self.t0 = t0
        if self.t0.tzinfo is None:
            self.t0 = self.t0.replace(tzinfo=timezone.utc)
            
        self.min_mic_level_threshold = min_mic_level_threshold
        self.speaker_activity_window_sec = timedelta(seconds=speaker_activity_window_sec)
        speaker_logger.info(f"TranscriptSpeakerMatcher initialized with t0: {self.t0.isoformat()}")

    def _create_speaker_activity_segments(self, speaker_meta_list: List[SpeakerMeta]) -> List[SpeakerActivitySegment]:
        """
        Converts raw SpeakerMeta entries (each representing a snapshot of all speakers' mic activity)
        into SpeakerActivitySegments, where each segment is a continuous period of a single speaker's activity.
        """
        activity_segments = []
        if not speaker_meta_list:
            return []

        sorted_meta = sorted(speaker_meta_list, key=lambda sm: (sm.timestamp, sm.user_id or ''))

        grouped_by_user = {}
        for sm in sorted_meta:
            if sm.user_id not in grouped_by_user:
                grouped_by_user[sm.user_id] = []
            grouped_by_user[sm.user_id].append(sm)

        for user_id, metas in grouped_by_user.items():
            if not metas: continue
            
            speaker_name = metas[0].name

            active_sub_segments = []

            for sm_entry in metas:
                meta_bits = sm_entry.meta_bits
                entry_timestamp = sm_entry.timestamp
                if not meta_bits:
                    continue

                num_bits = len(meta_bits)
                
                for i, bit in enumerate(meta_bits):
                    if bit == '1':
                        slot_start_time = entry_timestamp - timedelta(seconds=(num_bits - i) * 0.1)
                        slot_end_time = entry_timestamp - timedelta(seconds=(num_bits - i - 1) * 0.1)
                        active_sub_segments.append((slot_start_time, slot_end_time))
            
            if not active_sub_segments:
                continue

            active_sub_segments.sort(key=lambda x: x[0])

            merged_segments_for_user = []
            current_start, current_end = active_sub_segments[0]

            for i in range(1, len(active_sub_segments)):
                next_start, next_end = active_sub_segments[i]
                if next_start <= current_end:
                    current_end = max(current_end, next_end)
                else:
                    merged_segments_for_user.append(SpeakerActivitySegment(
                        speaker_name=speaker_name,
                        speaker_id=user_id,
                        start_time=current_start,
                        end_time=current_end,
                        avg_mic_level=1.0
                    ))
                    current_start, current_end = next_start, next_end
            
            merged_segments_for_user.append(SpeakerActivitySegment(
                speaker_name=speaker_name,
                speaker_id=user_id,
                start_time=current_start,
                end_time=current_end,
                avg_mic_level=1.0
            ))
            activity_segments.extend(merged_segments_for_user)
            
        activity_segments.sort(key=lambda s: s.start_time)
        speaker_logger.debug(f"Created {len(activity_segments)} speaker activity segments from raw meta data.")

        return activity_segments

    def match(self, speaker_meta_data: List[SpeakerMeta], transcription_segments: List[TranscriptSegment]) -> List[TranscriptSegment]:
        """Match transcripts with speakers based on temporal proximity and mic activity.
        
        Args:
            speaker_meta_data: List of speaker metadata derived from client's 'speaker_activity_update'.
            transcription_segments: List of TranscriptSegment objects to match with speakers.
            
        Returns:
            List of transcript segments with matched speaker_name and speaker_id.
        """
        if not speaker_meta_data or not transcription_segments:
            speaker_logger.info("Matcher: No speaker data or transcription segments to match.")
            return transcription_segments
        
        speaker_activity_periods = self._create_speaker_activity_segments(speaker_meta_data)

        if not speaker_activity_periods:
            speaker_logger.info("Matcher: No consolidated speaker activity periods created.")
            return transcription_segments

        for ts_segment in transcription_segments:
            abs_segment_start = self.t0 + timedelta(seconds=ts_segment.start_timestamp)
            abs_segment_end = self.t0 + timedelta(seconds=ts_segment.end_timestamp)
            
            segment_duration_td = abs_segment_end - abs_segment_start
            if segment_duration_td.total_seconds() <= 0:
                continue

            best_match_speaker_name: Optional[str] = None
            best_match_speaker_id: Optional[str] = None
            max_overlap_ratio = 0.0
            
            possible_matches = []

            for active_period in speaker_activity_periods:
                intersection_duration_td = active_period.intersection_with(abs_segment_start, abs_segment_end)
                
                if intersection_duration_td.total_seconds() > 0:
                    overlap_ratio = intersection_duration_td.total_seconds() / segment_duration_td.total_seconds()
                    possible_matches.append({
                        "speaker_name": active_period.speaker_name,
                        "speaker_id": active_period.speaker_id,
                        "overlap_ratio": overlap_ratio,
                        "mic_level": active_period.avg_mic_level
                    })
            
            if possible_matches:
                possible_matches.sort(key=lambda m: m["overlap_ratio"], reverse=True)
                
                best_match = possible_matches[0]
                
                if best_match["overlap_ratio"] > 0.5: 
                    ts_segment.speaker = best_match["speaker_name"]
                    ts_segment.speaker_id = best_match["speaker_id"]
                # else:
                    # speaker_logger.debug(f"    No significant match for '{ts_segment.content[:20]}'. Best overlap: {best_match['speaker_name']} ({best_match['overlap_ratio']:.2f})")

        return transcription_segments

# Transcription Collector client using Redis Streams
class TranscriptionCollectorClient:
    """Client that maintains connection to Redis on a separate thread
    and attempts auto-reconnection when the connection is lost."""

    def __init__(self, redis_stream_url=None):
        """Initialize client with redis connection URL.
        The connection will be established in a separate thread
        when connect() is called.
        
        Args:
            redis_stream_url: URL to redis server with the stream
        """
        # Use provided URL or environment variable with fallback to localhost
        self.redis_url = (
            redis_stream_url or 
            os.getenv("REDIS_STREAM_URL") or 
            "redis://localhost:6379/0"
        )
        
        self.redis_client = None
        self.is_connected = False
        self.connection_lock = threading.Lock()
        self.connection_thread = None
        self.stop_requested = False
        
        # Stream key for transcriptions
        self.stream_key = os.getenv("REDIS_STREAM_KEY", "transcription_segments")
        
        # Track session_uids for which we've published session_start events
        self.session_starts_published = set()
        
        # Connect on initialization 
        self.connect()

    def connect(self):
        """Connect to Redis in a separate thread with auto-reconnection."""
        with self.connection_lock:
            if self.connection_thread and self.connection_thread.is_alive():
                logging.info("Connection thread already running.")
                return
                
            self.stop_requested = False
            self.connection_thread = threading.Thread(
                target=self._connection_worker,
                daemon=True
            )
            self.connection_thread.start()
            logging.info("Started connection thread.")

    def _connection_worker(self):
        """Worker thread that establishes and maintains Redis connection.
        Handles automatic reconnection with exponential backoff."""
        retry_delay = 1  # Initial retry delay in seconds
        max_retry_delay = 30  # Maximum retry delay
        
        while not self.stop_requested:
            try:
                # Parse Redis URL
                logging.info(f"Connecting to Redis at {self.redis_url}")
                self.redis_client = redis.from_url(
                    self.redis_url,
                    decode_responses=True
                )
                
                # Test connection
                self.redis_client.ping()
                
                with self.connection_lock:
                    self.is_connected = True
                
                logging.info(f"Connected to Redis, stream key: {self.stream_key}")
                
                # Reset retry delay on successful connection
                retry_delay = 1
                
                # Keep connection alive
                while not self.stop_requested:
                    # Ping Redis to keep connection alive and check health
                    self.redis_client.ping()
                    time.sleep(5)  # Check connection every 5 seconds
                
            except redis.ConnectionError as e:
                logging.error(f"Redis connection error: {e}")
                with self.connection_lock:
                    self.is_connected = False
                    self.redis_client = None
                
            except Exception as e:
                logging.error(f"Redis error: {e}")
                with self.connection_lock:
                    self.is_connected = False
                    self.redis_client = None
            
            # Don't retry if stop was requested
            if self.stop_requested:
                break
                
            # Exponential backoff for retries
            logging.info(f"Retrying connection in {retry_delay} seconds...")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
    
    def disconnect(self):
        """Disconnect from Redis and stop the connection thread."""
        with self.connection_lock:
            self.stop_requested = True
            self.is_connected = False
            
            if self.redis_client:
                try:
                    self.redis_client.close()
                except Exception as e:
                    logging.error(f"Error closing Redis connection: {e}")
                self.redis_client = None
            
        # Wait for thread to terminate
        if self.connection_thread and self.connection_thread.is_alive():
            self.connection_thread.join(timeout=5.0)
            logging.info("Disconnected from Redis")

    def publish_session_start_event(self, token, platform, meeting_id, session_uid):
        """Publish a session_start event to the Redis stream.
        
        Args:
            token: User's API token
            platform: Platform identifier (e.g., 'google_meet') 
            meeting_id: Platform-specific meeting ID
            session_uid: Unique identifier for this session
        
        Returns:
            Boolean indicating success or failure
        """
        if session_uid in self.session_starts_published:
            logging.debug(f"Session start already published for {session_uid}")
            return True
            
        # Check connection
        if not self.is_connected or not self.redis_client:
            logging.warning("Cannot publish session_start: Not connected to Redis")
            return False
            
        # Validate required fields
        if not all([token, platform, meeting_id, session_uid]):
            logging.error("Missing required fields for session_start event")
            return False
            
        try:
            # Create event payload with ISO 8601 timestamp
            now = datetime.datetime.utcnow()
            timestamp_iso = now.isoformat() + "Z"
            
            payload = {
                "type": "session_start",
                "token": token,
                "platform": platform,
                "meeting_id": meeting_id,
                "uid": session_uid,
                "start_timestamp": timestamp_iso
            }
            
            # Publish to Redis stream
            message = {
                "payload": json.dumps(payload)
            }
            
            result = self.redis_client.xadd(
                self.stream_key,
                message
            )
            
            if result:
                logging.info(f"Published session_start event for session {session_uid}")
                # Mark this session as having a published start event
                self.session_starts_published.add(session_uid)
                return True
            else:
                logging.error(f"Failed to publish session_start event for {session_uid}")
                return False
                
        except Exception as e:
            logging.error(f"Error publishing session_start event: {e}")
            return False

    def send_transcription(self, token, platform, meeting_id, segments, session_uid=None, speaker_id=None, speaker_name=None):
        """Send transcription segments to Redis stream.
        
        Args:
            token: User's API token
            platform: Platform identifier (e.g., 'google_meet') 
            meeting_id: Platform-specific meeting ID
            segments: List of transcription segments
            session_uid: Optional unique identifier for this session
            speaker_id: Optional default speaker identifier (deprecated, use segment's speaker_id)
            speaker_name: Optional default speaker name (deprecated, use segment's speaker_name)
            
        Returns:
            Boolean indicating success or failure
        """
        # Check connection
        if not self.is_connected or not self.redis_client:
            logging.warning("Cannot send transcription: Not connected to Redis")
            return False
            
        # Validate required fields
        if not all([token, platform, meeting_id, segments]):
            logging.error("Missing required fields for transcription")
            return False
            
        # Generate a session_uid if not provided
        if not session_uid:
            session_uid = str(uuid.uuid4())
            
        # If this is the first time we're seeing this session_uid, publish a session_start event
        if session_uid not in self.session_starts_published:
            self.publish_session_start_event(token, platform, meeting_id, session_uid)
        
        try:
            # Create payload
            payload = {
                "type": "transcription",
                "token": token,
                "platform": platform, 
                "meeting_id": meeting_id,
                "segments": segments,  # Each segment should now include speaker_id and speaker_name
                "uid": session_uid,
                "language": None,  # Will be filled by transcription process
            }
            
            # Publish to Redis stream
            message = {
                "payload": json.dumps(payload)
            }
            
            result = self.redis_client.xadd(
                self.stream_key,
                message
            )
            
            if result:
                # Extract speaker name from the first segment with a speaker_name for logging
                segment_speaker = None
                for segment in segments:
                    if segment.get('speaker_name'):
                        segment_speaker = segment.get('speaker_name')
                        break
                
                logging.debug(f"Published transcription with {len(segments)} segments" + 
                             (f" for speaker {segment_speaker}" if segment_speaker else ""))
                return True
            else:
                logging.error("Failed to publish transcription")
                return False
                
        except Exception as e:
            logging.error(f"Error publishing transcription: {e}")
            return False

# Initialize collector client
collector_client = None
collector_url = os.environ.get("TRANSCRIPTION_COLLECTOR_URL")
redis_stream_url = os.environ.get("REDIS_STREAM_URL")

# Prefer REDIS_STREAM_URL if available, otherwise try to use TRANSCRIPTION_COLLECTOR_URL
if redis_stream_url:
    logging.info(f"Initializing transcription collector client with Redis Stream URL: {redis_stream_url}")
    collector_client = TranscriptionCollectorClient(redis_stream_url)
elif collector_url:
    # For backward compatibility, if the URL is not a Redis URL, log a warning
    if not collector_url.startswith("redis://"):
        logging.warning(f"WebSocket URL detected: {collector_url}. Should migrate to Redis Stream URL format.")
        # Still initialize with default Redis settings
        collector_client = TranscriptionCollectorClient()
    else:
        logging.info(f"Initializing transcription collector client with URL: {collector_url}")
        collector_client = TranscriptionCollectorClient(collector_url)
else:
    logging.info("No Redis Stream URL provided, initializing with default settings")
    collector_client = TranscriptionCollectorClient()

class ClientManager:
    def __init__(self, max_clients=4, max_connection_time=600):
        """
        Initializes the ClientManager with specified limits on client connections and connection durations.

        Args:
            max_clients (int, optional): The maximum number of simultaneous client connections allowed. Defaults to 4.
            max_connection_time (int, optional): The maximum duration (in seconds) a client can stay connected. Defaults
                                                 to 600 seconds (10 minutes).
        """
        self.clients = {}
        self.start_times = {}
        self.max_clients = max_clients
        self.max_connection_time = max_connection_time

    def add_client(self, websocket, client):
        """
        Adds a client and their connection start time to the tracking dictionaries.

        Args:
            websocket: The websocket associated with the client to add.
            client: The client object to be added and tracked.
        """
        self.clients[websocket] = client
        self.start_times[websocket] = time.time()

    def get_client(self, websocket):
        """
        Retrieves a client associated with the given websocket.

        Args:
            websocket: The websocket associated with the client to retrieve.

        Returns:
            The client object if found, False otherwise.
        """
        if websocket in self.clients:
            return self.clients[websocket]
        return False

    def remove_client(self, websocket):
        """
        Removes a client and their connection start time from the tracking dictionaries. Performs cleanup on the
        client if necessary.

        Args:
            websocket: The websocket associated with the client to be removed.
        """
        client = self.clients.pop(websocket, None)
        if client:
            client.cleanup()
        self.start_times.pop(websocket, None)

    def get_wait_time(self):
        """
        Calculates the estimated wait time for new clients based on the remaining connection times of current clients.

        Returns:
            The estimated wait time in minutes for new clients to connect. Returns 0 if there are available slots.
        """
        wait_time = None
        for start_time in self.start_times.values():
            current_client_time_remaining = self.max_connection_time - (time.time() - start_time)
            if wait_time is None or current_client_time_remaining < wait_time:
                wait_time = current_client_time_remaining
        return wait_time / 60 if wait_time is not None else 0

    def is_server_full(self, websocket, options):
        """
        Checks if the server is at its maximum client capacity and sends a wait message to the client if necessary.

        Args:
            websocket: The websocket of the client attempting to connect.
            options: A dictionary of options that may include the client's unique identifier.

        Returns:
            True if the server is full, False otherwise.
        """
        if len(self.clients) >= self.max_clients:
            wait_time = self.get_wait_time()
            response = {"uid": options["uid"], "status": "WAIT", "message": wait_time}
            websocket.send(json.dumps(response))
            return True
        return False

    def is_client_timeout(self, websocket):
        """
        Checks if a client has exceeded the maximum allowed connection time and disconnects them if so, issuing a warning.

        Args:
            websocket: The websocket associated with the client to check.

        Returns:
            True if the client's connection time has exceeded the maximum limit, False otherwise.
        """
        elapsed_time = time.time() - self.start_times[websocket]
        if elapsed_time >= self.max_connection_time:
            self.clients[websocket].disconnect()
            logging.warning(f"Client with uid '{self.clients[websocket].client_uid}' disconnected due to overtime.")
            return True
        return False


class BackendType(Enum):
    FASTER_WHISPER = "faster_whisper"
    TENSORRT = "tensorrt"

    @staticmethod
    def valid_types() -> List[str]:
        return [backend_type.value for backend_type in BackendType]

    @staticmethod
    def is_valid(backend: str) -> bool:
        return backend in BackendType.valid_types()

    def is_faster_whisper(self) -> bool:
        return self == BackendType.FASTER_WHISPER

    def is_tensorrt(self) -> bool:
        return self == BackendType.TENSORRT


class TranscriptionServer:
    RATE = 16000

    def __init__(self):
        self.client_manager = None
        self.no_voice_activity_chunks = 0
        self.use_vad = True
        self.single_model = False
        # Flag to track if server is healthy
        self.is_healthy = False
        # Health check HTTP server
        self.health_server = None

    def initialize_client(
        self, websocket, options, faster_whisper_custom_model_path,
        whisper_tensorrt_path, trt_multilingual
    ):
        client: Optional[ServeClientBase] = None

        # Extract and log the critical fields
        platform = options.get("platform")
        meeting_url = options.get("meeting_url")
        token = options.get("token")
        meeting_id = options.get("meeting_id")  # Extract meeting_id from options
        logging.info(f"Initializing client with uid={options['uid']}, platform={platform}, meeting_url={meeting_url}, token={token}, meeting_id={meeting_id}")
        
        if not platform or not meeting_url or not token:
            logging.warning(f"Missing critical fields for client {options['uid']}: platform={platform}, meeting_url={meeting_url}, token={token}")

        if self.backend.is_tensorrt():
            try:
                client = ServeClientTensorRT(
                    websocket,
                    multilingual=trt_multilingual,
                    language=options["language"],
                    task=options["task"],
                    client_uid=options["uid"],
                    platform=platform,
                    meeting_url=meeting_url,
                    token=token,
                    meeting_id=meeting_id,  # Pass meeting_id to constructor
                    model=whisper_tensorrt_path,
                    single_model=self.single_model,
                )
                logging.info("Running TensorRT backend.")
            except Exception as e:
                logging.error(f"TensorRT-LLM not supported: {e}")
                self.client_uid = options["uid"]
                websocket.send(json.dumps({
                    "uid": self.client_uid,
                    "status": "WARNING",
                    "message": "TensorRT-LLM not supported on Server yet. "
                               "Reverting to available backend: 'faster_whisper'"
                }))
                self.backend = BackendType.FASTER_WHISPER

        try:
            if self.backend.is_faster_whisper():
                if faster_whisper_custom_model_path is not None and os.path.exists(faster_whisper_custom_model_path):
                    logging.info(f"Using custom model {faster_whisper_custom_model_path}")
                    options["model"] = faster_whisper_custom_model_path
                client = ServeClientFasterWhisper(
                    websocket,
                    language=options["language"],
                    task=options["task"],
                    client_uid=options["uid"],
                    platform=platform,
                    meeting_url=meeting_url,
                    token=token,
                    meeting_id=meeting_id,  # Pass meeting_id to constructor
                    model=options["model"],
                    initial_prompt=options.get("initial_prompt"),
                    vad_parameters=options.get("vad_parameters"),
                    use_vad=self.use_vad,
                    single_model=self.single_model,
                )

                logging.info("Running faster_whisper backend.")
        except Exception as e:
            return

        if client is None:
            raise ValueError(f"Backend type {backend.value} not recognised or not handled.")

        self.client_manager.add_client(websocket, client)

    def get_audio_from_websocket(self, websocket):
        """
        Receives audio buffer from websocket and creates a numpy array out of it.
        Also handles text messages like speaker updates.

        Args:
            websocket: The websocket to receive audio from.

        Returns:
            A numpy array containing the audio, or False for non-audio data.
        """
        frame_data = websocket.recv()
        
        # Check if this is a text message rather than binary audio
        if isinstance(frame_data, str):
            self.process_websocket_message(websocket, frame_data)
            return False  # Signal to call get_audio_from_websocket again
        
        if frame_data == b"END_OF_AUDIO":
            return False
        
        return np.frombuffer(frame_data, dtype=np.float32)

    def handle_new_connection(self, websocket, faster_whisper_custom_model_path,
                              whisper_tensorrt_path, trt_multilingual):
        try:
            logging.info("New client connected")
            options = websocket.recv()
            options = json.loads(options)
            
            # Validate required parameters
            required_fields = ["uid", "platform", "meeting_url", "token", "meeting_id"]
            missing_fields = [field for field in required_fields if field not in options or not options[field]]
            
            if missing_fields:
                error_msg = f"Missing required fields: {', '.join(missing_fields)}"
                logging.error(error_msg)
                websocket.send(json.dumps({
                    "uid": options.get("uid", "unknown"),
                    "status": "ERROR",
                    "message": error_msg
                }))
                websocket.close()
                return False
                
            # Log the connection with critical parameters
            logging.info(f"Connection parameters received: uid={options['uid']}, platform={options['platform']}, meeting_url={options['meeting_url']}, token={options['token']}, meeting_id={options['meeting_id']}")

            if self.client_manager is None:
                max_clients = options.get('max_clients', 4)
                max_connection_time = options.get('max_connection_time', 600)
                self.client_manager = ClientManager(max_clients, max_connection_time)

            self.use_vad = options.get('use_vad')
            if self.client_manager.is_server_full(websocket, options):
                websocket.close()
                return False  # Indicates that the connection should not continue

            if self.backend.is_tensorrt():
                self.vad_detector = VoiceActivityDetector(frame_rate=self.RATE)
            self.initialize_client(websocket, options, faster_whisper_custom_model_path,
                                   whisper_tensorrt_path, trt_multilingual)
            return True
        except json.JSONDecodeError:
            logging.error("Failed to decode JSON from client")
            return False
        except ConnectionClosed:
            logging.info("Connection closed by client")
            return False
        except Exception as e:
            logging.error(f"Error during new connection initialization: {str(e)}")
            return False

    def process_audio_frames(self, websocket):
        try:
            # Try to get audio data
            frame_data = websocket.recv()
            
            # Check if this is a text message (JSON) rather than binary audio data
            if isinstance(frame_data, str):
                return self.process_websocket_message(websocket, frame_data)
            
            # If it's "END_OF_AUDIO" marker
            if frame_data == b"END_OF_AUDIO":
                client = self.client_manager.get_client(websocket)
                if client and self.backend.is_tensorrt():
                    client.set_eos(True)
                return False
            
            # Otherwise, process as normal audio data
            frame_np = np.frombuffer(frame_data, dtype=np.float32)
            client = self.client_manager.get_client(websocket)
            
            if self.backend.is_tensorrt():
                voice_active = self.voice_activity(websocket, frame_np)
                if voice_active:
                    self.no_voice_activity_chunks = 0
                    client.set_eos(False)
                if self.use_vad and not voice_active:
                    return True

            client.add_frames(frame_np)
            return True
        
        except Exception as e:
            logging.error(f"Error processing frame data: {e}")
            return True  # Continue processing to be safe

    def recv_audio(self,
                   websocket,
                   backend: BackendType = BackendType.FASTER_WHISPER,
                   faster_whisper_custom_model_path=None,
                   whisper_tensorrt_path=None,
                   trt_multilingual=False):
        """
        Receive audio chunks and control messages from a client in an infinite loop.

        Continuously receives audio frames from a connected client
        over a WebSocket connection. It processes the audio frames using a
        voice activity detection (VAD) model to determine if they contain speech
        or not. If the audio frame contains speech, it is added to the client's
        audio data for ASR.
        If the maximum number of clients is reached, the method sends a
        "WAIT" status to the client, indicating that they should wait
        until a slot is available.
        If a client's connection exceeds the maximum allowed time, it will
        be disconnected, and the client's resources will be cleaned up.
        This method continuously receives data frames (audio or JSON control messages)
        and processes them accordingly.

        Args:
            websocket (WebSocket): The WebSocket connection for the client.
            backend (str): The backend to run the server with.
            faster_whisper_custom_model_path (str): path to custom faster whisper model.
            whisper_tensorrt_path (str): Required for tensorrt backend.
            trt_multilingual(bool): Only used for tensorrt, True if multilingual model.

        Raises:
            Exception: If there is an error during processing.
        """
        self.backend = backend
        if not self.handle_new_connection(websocket, faster_whisper_custom_model_path,
                                          whisper_tensorrt_path, trt_multilingual):
            return

        try:
            while not self.client_manager.is_client_timeout(websocket):
                if not self.process_audio_frames(websocket):
                    break
        except ConnectionClosed:
            logging.info("Connection closed by client")
        except Exception as e:
            logging.error(f"Unexpected error: {str(e)}")
        finally:
            if self.client_manager.get_client(websocket):
                self.cleanup(websocket)
                websocket.close()
            del websocket

    def run(self,
            host,
            # port=9090, #GPU version
            port=9092, #CPU version
            backend="tensorrt",
            faster_whisper_custom_model_path=None,
            whisper_tensorrt_path=None,
            trt_multilingual=False,
            single_model=False):
        """
        Run the transcription server.

        Args:
            host (str): The host address to bind the server.
            port (int): The port number to bind the server.
        """
        if faster_whisper_custom_model_path is not None and not os.path.exists(faster_whisper_custom_model_path):
            raise ValueError(f"Custom faster_whisper model '{faster_whisper_custom_model_path}' is not a valid path.")
        if whisper_tensorrt_path is not None and not os.path.exists(whisper_tensorrt_path):
            raise ValueError(f"TensorRT model '{whisper_tensorrt_path}' is not a valid path.")
        if single_model:
            if faster_whisper_custom_model_path or whisper_tensorrt_path:
                logging.info("Custom model option was provided. Switching to single model mode.")
                self.single_model = True
                # TODO: load model initially
            else:
                logging.info("Single model mode currently only works with custom models.")
        if not BackendType.is_valid(backend):
            raise ValueError(f"{backend} is not a valid backend type. Choose backend from {BackendType.valid_types()}")
            
        # Log server startup information
        logger.info(f"SERVER_START: host={host}, port={port}, backend={backend}, single_model={single_model}")
        
        # Start health check HTTP server on port+1
        self.start_health_check_server(host, port + 1)
        
        with serve(
            functools.partial(
                self.recv_audio,
                backend=BackendType(backend),
                faster_whisper_custom_model_path=faster_whisper_custom_model_path,
                whisper_tensorrt_path=whisper_tensorrt_path,
                trt_multilingual=trt_multilingual
            ),
            host,
            port
        ) as server:
            # Mark server as healthy once websocket server is running
            self.is_healthy = True
            logger.info(f"SERVER_RUNNING: WhisperLive server running on {host}:{port} with health check on {host}:{port+1}/health")
            server.serve_forever()

    def voice_activity(self, websocket, frame_np):
        """
        Evaluates the voice activity in a given audio frame and manages the state of voice activity detection.

        This method uses the configured voice activity detection (VAD) model to assess whether the given audio frame
        contains speech. If the VAD model detects no voice activity for more than three consecutive frames,
        it sets an end-of-speech (EOS) flag for the associated client. This method aims to efficiently manage
        speech detection to improve subsequent processing steps.

        Args:
            websocket: The websocket associated with the current client. Used to retrieve the client object
                    from the client manager for state management.
            frame_np (numpy.ndarray): The audio frame to be analyzed. This should be a NumPy array containing
                                    the audio data for the current frame.

        Returns:
            bool: True if voice activity is detected in the current frame, False otherwise. When returning False
                after detecting no voice activity for more than three consecutive frames, it also triggers the
                end-of-speech (EOS) flag for the client.
        """
        if not self.vad_detector(frame_np):
            self.no_voice_activity_chunks += 1
            if self.no_voice_activity_chunks > 3:
                client = self.client_manager.get_client(websocket)
                if not client.eos:
                    client.set_eos(True)
                time.sleep(0.1)    # Sleep 100m; wait some voice activity.
            return False
        return True

    def cleanup(self, websocket):
        """
        Cleans up resources associated with a given client's websocket.

        Args:
            websocket: The websocket associated with the client to be cleaned up.
        """
        if self.client_manager.get_client(websocket):
            self.client_manager.remove_client(websocket)

    def start_health_check_server(self, host, port):
        """Start a simple HTTP server for health checks.
        
        This runs in a separate thread and listens on a different port than the WebSocket server.
        """
        class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):
            parent_server = self
            
            def do_GET(self):
                if self.path == '/health':
                    if self.parent_server.is_healthy:
                        self.send_response(200)
                        self.send_header('Content-type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(b'OK')
                    else:
                        self.send_response(503)
                        self.send_header('Content-type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(b'Service Unavailable')
                else:
                    self.send_response(404)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b'Not Found')
            
            # Silence server logs
            def log_message(self, format, *args):
                return
        
        # Create HTTP server for health checks
        try:
            handler = HealthCheckHandler
            self.health_server = socketserver.TCPServer((host, port), handler)
            
            # Start server in a new thread
            health_thread = threading.Thread(target=self.health_server.serve_forever)
            health_thread.daemon = True  # So it stops when the main thread stops
            health_thread.start()
            
            logging.info(f"Health check HTTP server started on {host}:{port}")
        except Exception as e:
            logging.error(f"Failed to start health check server: {e}")

    def process_websocket_message(self, websocket, message_data):
        """Process non-audio WebSocket messages such as speaker updates."""
        try:
            message = json.loads(message_data)
            client = self.client_manager.get_client(websocket)
            
            # Handle speaker update messages
            if message.get("type") == "speaker_update" and client:
                speaker_id = message.get("speaker_id")
                speaker_name = message.get("speaker_name")
                client.update_speaker(speaker_id, speaker_name)
                return True
            
            return False  # Not a recognized message type
        except json.JSONDecodeError:
            logging.error("Failed to decode JSON message")
            return False
        except Exception as e:
            logging.error(f"Error processing WebSocket message: {e}")
            return False


class ServeClientBase(object):
    RATE = 16000
    SERVER_READY = "SERVER_READY"
    DISCONNECT = "DISCONNECT"

    def __init__(self, websocket, language="en", task="transcribe", client_uid=None, platform=None, meeting_url=None, token=None, meeting_id=None):
        self.websocket = websocket
        self.language = language
        self.task = task
        self.client_uid = client_uid or str(uuid.uuid4())
        self.platform = platform
        self.meeting_url = meeting_url
        self.token = token
        self.meeting_id = meeting_id
        
        # Timestamps and speaker data handling
        self.transcription_start_time = dt.now(timezone.utc) # t0 for this client session, ensure timezone aware
        self.speaker_activity_data: List[SpeakerMeta] = [] # Store SpeakerMeta objects
        self.transcript_speaker_matcher = TranscriptSpeakerMatcher(t0=self.transcription_start_time)
        speaker_logger.info(f"ServeClient {self.client_uid} initialized with t0: {self.transcription_start_time.isoformat()}")

        # Old speaker tracking - can be removed or refactored if new system covers it.
        # self.current_speaker_id = None 
        # self.current_speaker_name = None
        # self.speaker_update_time = None
        # self.speaker_timeline = []
        
        self.transcription_buffer = TranscriptionBuffer(self.client_uid)
        
        # Enhanced speaker tracking
        self.current_speaker_id = None
        self.current_speaker_name = None
        self.speaker_update_time = None
        self.speaker_timeline = []  # List of {timestamp, iso_timestamp, speaker_id, speaker_name} entries
        
        self.frames = b""
        self.timestamp_offset = 0.0
        self.frames_np = None
        self.frames_offset = 0.0
        self.text = []
        self.current_out = ''
        self.prev_out = ''
        self.t_start = None
        self.exit = False
        self.same_output_count = 0
        self.show_prev_out_thresh = 5   # if pause(no output from whisper) show previous output for 5 seconds
        self.add_pause_thresh = 3       # add a blank to segment list as a pause(no speech) for 3 seconds
        self.transcript = []
        self.send_last_n_segments = 10

        # text formatting
        self.pick_previous_segments = 2

        # threading
        self.lock = threading.Lock()
        
        # Send SERVER_READY message
        ready_message = json.dumps({"status": self.SERVER_READY, "uid": self.client_uid})
        logging.info(f"Client {self.client_uid} connected. Sending SERVER_READY.")
        self.websocket.send(ready_message)
        
        # Publish session_start event first if using Redis collector
        if collector_client and all([platform, meeting_url, token, meeting_id]):
            # Publish session start event
            collector_client.publish_session_start_event(token, platform, meeting_id, self.client_uid)
            logging.info(f"Published session_start event for client {self.client_uid}")

    def speech_to_text(self):
        raise NotImplementedError

    def transcribe_audio(self):
        raise NotImplementedError

    def handle_transcription_output(self):
        raise NotImplementedError

    def add_frames(self, frame_np):
        """
        Add audio frames to the ongoing audio stream buffer.

        This method is responsible for maintaining the audio stream buffer, allowing the continuous addition
        of audio frames as they are received. It also ensures that the buffer does not exceed a specified size
        to prevent excessive memory usage.

        If the buffer size exceeds a threshold (45 seconds of audio data), it discards the oldest 30 seconds
        of audio data to maintain a reasonable buffer size. If the buffer is empty, it initializes it with the provided
        audio frame. The audio stream buffer is used for real-time processing of audio data for transcription.

        Args:
            frame_np (numpy.ndarray): The audio frame data as a NumPy array.

        """
        self.lock.acquire()
        if self.frames_np is not None and self.frames_np.shape[0] > 45*self.RATE:
            self.frames_offset += 30.0
            self.frames_np = self.frames_np[int(30*self.RATE):]
            # check timestamp offset(should be >= self.frame_offset)
            # this basically means that there is no speech as timestamp offset hasnt updated
            # and is less than frame_offset
            if self.timestamp_offset < self.frames_offset:
                self.timestamp_offset = self.frames_offset
        if self.frames_np is None:
            self.frames_np = frame_np.copy()
        else:
            self.frames_np = np.concatenate((self.frames_np, frame_np), axis=0)
        self.lock.release()

    def clip_audio_if_no_valid_segment(self):
        """
        Update the timestamp offset based on audio buffer status.
        Clip audio if the current chunk exceeds 30 seconds, this basically implies that
        no valid segment for the last 30 seconds from whisper
        """
        with self.lock:
            if self.frames_np[int((self.timestamp_offset - self.frames_offset)*self.RATE):].shape[0] > 25 * self.RATE:
                duration = self.frames_np.shape[0] / self.RATE
                self.timestamp_offset = self.frames_offset + duration - 5

    def get_audio_chunk_for_processing(self):
        """
        Retrieves the next chunk of audio data for processing based on the current offsets.

        Calculates which part of the audio data should be processed next, based on
        the difference between the current timestamp offset and the frame's offset, scaled by
        the audio sample rate (RATE). It then returns this chunk of audio data along with its
        duration in seconds.

        Returns:
            tuple: A tuple containing:
                - input_bytes (np.ndarray): The next chunk of audio data to be processed.
                - duration (float): The duration of the audio chunk in seconds.
        """
        with self.lock:
            samples_take = max(0, (self.timestamp_offset - self.frames_offset) * self.RATE)
            input_bytes = self.frames_np[int(samples_take):].copy()
        duration = input_bytes.shape[0] / self.RATE
        return input_bytes, duration

    def prepare_segments(self, last_segment=None):
        """
        Prepares the segments of transcribed text to be sent to the client.

        This method compiles the recent segments of transcribed text, ensuring that only the
        specified number of the most recent segments are included. It also appends the most
        recent segment of text if provided (which is considered incomplete because of the possibility
        of the last word being truncated in the audio chunk).

        Args:
            last_segment (str, optional): The most recent segment of transcribed text to be added
                                          to the list of segments. Defaults to None.

        Returns:
            list: A list of transcribed text segments to be sent to the client.
        """
        segments = []
        if len(self.transcript) >= self.send_last_n_segments:
            segments = self.transcript[-self.send_last_n_segments:].copy()
        else:
            segments = self.transcript.copy()
        if last_segment is not None:
            segments = segments + [last_segment]
        return segments

    def get_audio_chunk_duration(self, input_bytes):
        """
        Calculates the duration of the provided audio chunk.

        Args:
            input_bytes (numpy.ndarray): The audio chunk for which to calculate the duration.

        Returns:
            float: The duration of the audio chunk in seconds.
        """
        return input_bytes.shape[0] / self.RATE

    def send_transcription_to_client(self, segments):
        """
        Sends the specified transcription segments to the client over the websocket connection.
        This method now also handles matching speakers to segments before sending.

        Args:
            segments (list): A list of raw transcription segments (dictionaries) from Whisper.
        """
        try:
            if not self.platform or not self.meeting_url or not self.token:
                logging.error(f"ERROR: Missing required fields for client {self.client_uid}: platform={self.platform}, meeting_url={self.meeting_url}, token={self.token}")
                return

            # 1. Convert raw Whisper segments to TranscriptSegment objects
            processed_transcript_segments: List[TranscriptSegment] = []
            for seg_data in segments: # Assuming segments is a list of dicts from Whisper
                try:
                    # Pass self.transcription_start_time as t0 for relative to absolute conversion
                    ts_seg = TranscriptSegment.from_whisper_live_segment(seg_data, t0_timestamp=self.transcription_start_time)
                    processed_transcript_segments.append(ts_seg)
                except ValueError as e:
                    speaker_logger.error(f"Skipping invalid Whisper segment: {seg_data}. Error: {e}")
                    continue
            
            # 2. Match speakers to these TranscriptSegment objects
            # Lock speaker_activity_data if it can be modified by another thread (e.g., process_websocket_message)
            # For simplicity, direct access here assumes process_websocket_message updates it sequentially or with its own locks if needed.
            # It might be safer to copy self.speaker_activity_data if there's a risk of concurrent modification during matching.
            current_speaker_activity_snapshot = self.speaker_activity_data.copy() # Take a snapshot for matching

            matched_segments = self.transcript_speaker_matcher.match(
                current_speaker_activity_snapshot, 
                processed_transcript_segments
            )

            # 3. Convert matched TranscriptSegment objects back to dictionaries for sending
            segments_to_send = [ts.to_dict() for ts in matched_segments]

            data = {
                "uid": self.client_uid,
                "segments": segments_to_send, # Send the matched segments
            }
            self.websocket.send(json.dumps(data))
            
            # Send to transcription collector if available
            global collector_client
            if collector_client:
                # Send transcription to Redis stream with speaker info
                collector_client.send_transcription(
                    token=self.token,
                    platform=self.platform,
                    meeting_id=self.meeting_id,
                    segments=segments_to_send, # Send matched segments to collector as well
                    session_uid=self.client_uid,
                    # speaker_id and speaker_name are now part of each segment in segments_to_send
                )
             # Log the transcription data to file with more detailed formatting 
            formatted_log_segments = []
            for i, seg_dict in enumerate(segments_to_send):
                speaker_name = seg_dict.get('speaker_name', 'Unknown')
                speaker_id = seg_dict.get('speaker_id', 'N/A')
                text_content = seg_dict.get('text', '')
                start_time = seg_dict.get('start', 'N/A')
                end_time = seg_dict.get('end', 'N/A')
                completed_status = 'COMPLETE' if seg_dict.get('completed', False) else 'PARTIAL' # Assuming 'completed' might be added to to_dict if needed

                formatted_log_segments.append(
                    f"[{i}] ({start_time}-{end_time}) "
                    f"[{completed_status}] " 
                    f"[Speaker: {speaker_name} ({speaker_id})]: "
                    f"\"{text_content}\""
                )
                    
            logger.info(f"TRANSCRIPTION: client={self.client_uid}, platform={self.platform}, meeting_url={self.meeting_url}, token={self.token}, meeting_id={self.meeting_id}, segments=\n" + "\n".join(formatted_log_segments))
        except Exception as e:
            logging.error(f"[ERROR]: Sending data to client: {e}", exc_info=True)

    def disconnect(self):
        """
        Notify the client of disconnection and send a disconnect message.

        This method sends a disconnect message to the client via the WebSocket connection to notify them
        that the transcription service is disconnecting gracefully.

        """
        self.websocket.send(json.dumps({
            "uid": self.client_uid,
            "message": self.DISCONNECT
        }))

    def cleanup(self):
        """
        Perform cleanup tasks before exiting the transcription service.

        This method performs necessary cleanup tasks, including stopping the transcription thread, marking
        the exit flag to indicate the transcription thread should exit gracefully, and destroying resources
        associated with the transcription process.

        """
        logging.info("Cleaning up.")
        self.exit = True

    def forward_to_collector(self, segments):
        """Forward transcriptions to the collector if available"""
        if collector_client and segments:
            # Send transcription to collector
            collector_client.send_transcription(
                token=self.token,
                platform=self.platform,
                meeting_id=self.meeting_id,
                segments=segments,
                session_uid=self.client_uid,
                speaker_id=self.current_speaker_id,
                speaker_name=self.current_speaker_name
            )

    def update_speaker(self, speaker_id, speaker_name):
        """Update the current active speaker information and record in timeline."""
        timestamp = datetime.datetime.utcnow()
        iso_timestamp = timestamp.isoformat() + "Z"
        
        # Don't update to None if we already have a speaker - only record in timeline
        if speaker_id is None and speaker_name is None and self.current_speaker_id is not None:
            logging.info(f"Ignoring None speaker update for {self.client_uid} - keeping current speaker: {self.current_speaker_name} ({self.current_speaker_id})")
            return
        
        # Record in timeline
        self.speaker_timeline.append({
            "timestamp": timestamp,  # Store datetime object for comparisons
            "iso_timestamp": iso_timestamp,  # Store string for JSON
            "speaker_id": speaker_id,
            "speaker_name": speaker_name
        })
        
        # Update current speaker
        self.current_speaker_id = speaker_id
        self.current_speaker_name = speaker_name
        self.speaker_update_time = iso_timestamp
        
        logging.info(f"Speaker updated for {self.client_uid}: {speaker_name} ({speaker_id})")
        logger.info(f"SPEAKER_UPDATE: client={self.client_uid}, speaker_id={speaker_id}, speaker_name={speaker_name}")

    def get_speaker_at_time(self, start_time, end_time):
        """Find the speaker active during the given time period.
        
        Args:
            start_time (float): Start time of the segment in seconds
            end_time (float): End time of the segment in seconds
            
        Returns:
            tuple: (speaker_id, speaker_name) or (None, None) if no speaker found
        """
        if not self.speaker_timeline:
            return (self.current_speaker_id, self.current_speaker_name)
        
        # Calculate the timestamp for this segment's time
        # Convert segment time (which is in seconds from start of transcription) to a timestamp
        segment_midpoint = (start_time + end_time) / 2
        segment_delta = datetime.timedelta(seconds=segment_midpoint)
        segment_timestamp = self.transcription_start_time + segment_delta
        
        # Find the latest speaker update before or at this segment's time
        latest_speaker = None
        for entry in reversed(self.speaker_timeline):
            if entry["timestamp"] <= segment_timestamp:
                latest_speaker = entry
                break
        
        if latest_speaker:
            return (latest_speaker["speaker_id"], latest_speaker["speaker_name"])
        
        # If no speaker found before this segment, use current
        return (self.current_speaker_id, self.current_speaker_name)


class ServeClientTensorRT(ServeClientBase):

    SINGLE_MODEL = None
    SINGLE_MODEL_LOCK = threading.Lock()

    def __init__(self, websocket, task="transcribe", multilingual=False, language=None, client_uid=None, model=None, single_model=False, platform=None, meeting_url=None, token=None, meeting_id=None):
        """
        Initialize a ServeClient instance.
        The Whisper model is initialized based on the client's language and device availability.
        The transcription thread is started upon initialization. A "SERVER_READY" message is sent
        to the client to indicate that the server is ready.

        Args:
            websocket (WebSocket): The WebSocket connection for the client.
            task (str, optional): The task type, e.g., "transcribe." Defaults to "transcribe".
            device (str, optional): The device type for Whisper, "cuda" or "cpu". Defaults to None.
            multilingual (bool, optional): Whether the client supports multilingual transcription. Defaults to False.
            language (str, optional): The language for transcription. Defaults to None.
            client_uid (str, optional): A unique identifier for the client. Defaults to None.
            single_model (bool, optional): Whether to instantiate a new model for each client connection. Defaults to False.
            platform (str, optional): The platform identifier (e.g., "google_meet"). Defaults to None.
            meeting_url (str, optional): The URL of the meeting. Defaults to None.
            token (str, optional): The token to use for identifying the client. Defaults to None.
        """
        super().__init__(websocket, language, task, client_uid, platform, meeting_url, token, meeting_id)
        self.eos = False
        
        # Log the critical parameters
        logging.info(f"Initializing TensorRT client {client_uid} with platform={platform}, meeting_url={meeting_url}, token={token}")

        if single_model:
            if ServeClientTensorRT.SINGLE_MODEL is None:
                self.create_model(model, multilingual)
                ServeClientTensorRT.SINGLE_MODEL = self.transcriber
            else:
                self.transcriber = ServeClientTensorRT.SINGLE_MODEL
        else:
            self.create_model(model, multilingual)

        # threading
        self.trans_thread = threading.Thread(target=self.speech_to_text)
        self.trans_thread.start()

        self.websocket.send(json.dumps({
            "uid": self.client_uid,
            "message": self.SERVER_READY,
            "backend": "tensorrt"
        }))

    def create_model(self, model, multilingual, warmup=True):
        """
        Instantiates a new model, sets it as the transcriber and does warmup if desired.
        """
        self.transcriber = WhisperTRTLLM(
            model,
            assets_dir="assets",
            device="cuda", #NOTE: why is this hard coded?
            is_multilingual=multilingual,
            language=self.language,
            task=self.task
        )
        if warmup:
            self.warmup()

    def warmup(self, warmup_steps=10):
        """
        Warmup TensorRT since first few inferences are slow.

        Args:
            warmup_steps (int): Number of steps to warm up the model for.
        """
        logging.info("[INFO:] Warming up TensorRT engine..")
        mel, _ = self.transcriber.log_mel_spectrogram("assets/jfk.flac")
        for i in range(warmup_steps):
            self.transcriber.transcribe(mel)

    def set_eos(self, eos):
        """
        Sets the End of Speech (EOS) flag.

        Args:
            eos (bool): The value to set for the EOS flag.
        """
        self.lock.acquire()
        self.eos = eos
        self.lock.release()

    def handle_transcription_output(self, last_segment, duration):
        """
        Handle the transcription output, updating the transcript and sending data to the client.

        Args:
            last_segment (str): The last segment from the whisper output which is considered to be incomplete because
                                of the possibility of word being truncated.
            duration (float): Duration of the transcribed audio chunk.
        """
        segments = self.prepare_segments({"text": last_segment})
        self.send_transcription_to_client(segments)
        if self.eos:
            self.update_timestamp_offset(last_segment, duration)

    def transcribe_audio(self, input_bytes):
        """
        Transcribe the audio chunk and send the results to the client.

        Args:
            input_bytes (np.array): The audio chunk to transcribe.
        """
        if ServeClientTensorRT.SINGLE_MODEL:
            ServeClientTensorRT.SINGLE_MODEL_LOCK.acquire()
        logging.info(f"[WhisperTensorRT:] Processing audio with duration: {input_bytes.shape[0] / self.RATE}")
        mel, duration = self.transcriber.log_mel_spectrogram(input_bytes)
        last_segment = self.transcriber.transcribe(
            mel,
            text_prefix=f"<|startoftranscript|><|{self.language}|><|{self.task}|><|notimestamps|>"
        )
        if ServeClientTensorRT.SINGLE_MODEL:
            ServeClientTensorRT.SINGLE_MODEL_LOCK.release()
        if last_segment:
            self.handle_transcription_output(last_segment, duration)

    def update_timestamp_offset(self, last_segment, duration):
        """
        Update timestamp offset and transcript.

        Args:
            last_segment (str): Last transcribed audio from the whisper model.
            duration (float): Duration of the last audio chunk.
        """
        with self.lock:
            start_time = self.timestamp_offset
            end_time = self.timestamp_offset + duration
            
            if not len(self.transcript):
                self.transcript.append({
                    "text": last_segment + " ", 
                    "start": "{:.3f}".format(start_time),
                    "end": "{:.3f}".format(end_time),
                    "completed": True
                })
            elif self.transcript[-1]["text"].strip() != last_segment:
                self.transcript.append({
                    "text": last_segment + " ", 
                    "start": "{:.3f}".format(start_time),
                    "end": "{:.3f}".format(end_time),
                    "completed": True
                })
            
            self.timestamp_offset += duration

    def speech_to_text(self):
        """
        Process an audio stream in an infinite loop, continuously transcribing the speech.

        This method continuously receives audio frames, performs real-time transcription, and sends
        transcribed segments to the client via a WebSocket connection.

        If the client's language is not detected, it waits for 30 seconds of audio input to make a language prediction.
        It utilizes the Whisper ASR model to transcribe the audio, continuously processing and streaming results. Segments
        are sent to the client in real-time, and a history of segments is maintained to provide context.Pauses in speech
        (no output from Whisper) are handled by showing the previous output for a set duration. A blank segment is added if
        there is no speech for a specified duration to indicate a pause.

        Raises:
            Exception: If there is an issue with audio processing or WebSocket communication.

        """
        while True:
            if self.exit:
                logging.info("Exiting speech to text thread")
                break

            if self.frames_np is None:
                time.sleep(0.02)    # wait for any audio to arrive
                continue

            self.clip_audio_if_no_valid_segment()

            input_bytes, duration = self.get_audio_chunk_for_processing()
            if duration < 0.4:
                continue

            try:
                input_sample = input_bytes.copy()
                logging.info(f"[WhisperTensorRT:] Processing audio with duration: {duration}")
                self.transcribe_audio(input_sample)

            except Exception as e:
                logging.error(f"[ERROR]: {e}")

    def send(self, partial_segments, completed_segments):
        # Add transcriptions to buffer
        self.transcription_buffer.add_segments(partial_segments, completed_segments)
        
        # Get formatted segments for the response
        response_segments = self.transcription_buffer.get_segments_for_response()
        
        # Forward completed segments to collector
        if completed_segments:
            self.forward_to_collector(completed_segments)
            
        # Construct and send response
        response = {
            "uid": self.client_uid,
            "segments": response_segments
        }
        
        try:
            self.websocket.send(json.dumps(response))
        except ConnectionClosed:
            logging.warning(f"Connection closed for client {self.client_uid} while sending transcription.")

    def process_websocket_message(self, websocket, message_data):
        """Process non-audio WebSocket messages such as speaker updates."""
        try:
            message = json.loads(message_data)
            client = self.client_manager.get_client(websocket)
            if not client:
                logging.warning(f"No client found for websocket in process_websocket_message")
                return False

            message_type = message.get("type")
            
            # Handle speaker_activity_update messages
            if message_type == "speaker_activity_update":
                speaker_logger.info(f"Received speaker_activity_update for client {client.client_uid}. Payload: {message}")
                new_speaker_metas = convert_speaker_activity_to_meta(message) # message is the full payload
                if new_speaker_metas:
                    # Potentially lock client.speaker_activity_data if updates can be frequent and concurrent with reads
                    client.speaker_activity_data.extend(new_speaker_metas)
                    speaker_logger.info(f"Added {len(new_speaker_metas)} SpeakerMeta entries for client {client.client_uid}. Total now: {len(client.speaker_activity_data)}")
                return True # Message handled
            
            # Handle other message types if necessary
            # elif message_type == "some_other_control_message":
            #     # ... handle it ...
            #     return True

            speaker_logger.debug(f"Received unhandled JSON message type: {message_type} for client {client.client_uid}")
            return False  # Not a recognized or handled message type here
        except json.JSONDecodeError:
            logging.error("Failed to decode JSON message in process_websocket_message")
            return False
        except Exception as e:
            logging.error(f"Error processing WebSocket message in process_websocket_message: {e}", exc_info=True)
            return False


class ServeClientFasterWhisper(ServeClientBase):

    SINGLE_MODEL = None
    SINGLE_MODEL_LOCK = threading.Lock()

    def __init__(self, websocket, task="transcribe", device=None, language=None, client_uid=None, model="small.en",
                 initial_prompt=None, vad_parameters=None, use_vad=True, single_model=False, platform=None, meeting_url=None, token=None, meeting_id=None):
        """
        Initialize a ServeClient instance.
        The Whisper model is initialized based on the client's language and device availability.
        The transcription thread is started upon initialization. A "SERVER_READY" message is sent
        to the client to indicate that the server is ready.

        Args:
            websocket (WebSocket): The WebSocket connection for the client.
            task (str, optional): The task type, e.g., "transcribe." Defaults to "transcribe".
            device (str, optional): The device type for Whisper, "cuda" or "cpu". Defaults to None.
            language (str, optional): The language for transcription. Defaults to None.
            client_uid (str, optional): A unique identifier for the client. Defaults to None.
            model (str, optional): The whisper model size. Defaults to 'small.en'
            initial_prompt (str, optional): Prompt for whisper inference. Defaults to None.
            single_model (bool, optional): Whether to instantiate a new model for each client connection. Defaults to False.
            platform (str, optional): The platform identifier (e.g., "google_meet"). Defaults to None.
            meeting_url (str, optional): The URL of the meeting. Defaults to None.
            token (str, optional): The token to use for identifying the client. Defaults to None.
        """
        super().__init__(websocket, language, task, client_uid, platform, meeting_url, token, meeting_id)
        self.model_sizes = [
            "tiny", "tiny.en", "base", "base.en", "small", "small.en",
            "medium", "medium.en", "large-v2", "large-v3", "distil-small.en",
            "distil-medium.en", "distil-large-v2", "distil-large-v3",
            "large-v3-turbo", "turbo"
        ]
        
        # Log the critical parameters
        logging.info(f"Initializing FasterWhisper client {client_uid} with platform={platform}, meeting_url={meeting_url}, token={token}")

        self.model_size_or_path = model
        self.language = "en" if self.model_size_or_path.endswith("en") else language
        self.task = task
        self.initial_prompt = initial_prompt
        self.vad_parameters = vad_parameters or {"onset": 0.5}
        self.no_speech_thresh = 0.45
        self.same_output_threshold = 10
        self.end_time_for_same_output = None

        device = "cuda" if torch.cuda.is_available() else "cpu"
        if device == "cuda":
            major, _ = torch.cuda.get_device_capability(device)
            self.compute_type = "float16" if major >= 7 else "float32"
        else:
            self.compute_type = "default" #"int8" #NOTE: maybe we use default here...

        if self.model_size_or_path is None:
            return
        logging.info(f"Using Device={device} with precision {self.compute_type}")
    
        try:
            if single_model:
                if ServeClientFasterWhisper.SINGLE_MODEL is None:
                    self.create_model(device)
                    ServeClientFasterWhisper.SINGLE_MODEL = self.transcriber
                else:
                    self.transcriber = ServeClientFasterWhisper.SINGLE_MODEL
            else:
                self.create_model(device)
        except Exception as e:
            logging.error(f"Failed to load model: {e}")
            self.websocket.send(json.dumps({
                "uid": self.client_uid,
                "status": "ERROR",
                "message": f"Failed to load model: {str(self.model_size_or_path)}"
            }))
            self.websocket.close()
            return

        self.use_vad = use_vad

        # threading
        self.trans_thread = threading.Thread(target=self.speech_to_text)
        self.trans_thread.start()
        self.websocket.send(
            json.dumps(
                {
                    "uid": self.client_uid,
                    "message": self.SERVER_READY,
                    "backend": "faster_whisper"
                }
            )
        )

    def create_model(self, device):
        """
        Instantiates a new model, sets it as the transcriber.
        """
        self.transcriber = WhisperModel(
            self.model_size_or_path,
            device=device,
            compute_type=self.compute_type,
            local_files_only=False,
        )

    def check_valid_model(self, model_size):
        """
        Check if it's a valid whisper model size.

        Args:
            model_size (str): The name of the model size to check.

        Returns:
            str: The model size if valid, None otherwise.
        """
        if model_size not in self.model_sizes:
            self.websocket.send(
                json.dumps(
                    {
                        "uid": self.client_uid,
                        "status": "ERROR",
                        "message": f"Invalid model size {model_size}. Available choices: {self.model_sizes}"
                    }
                )
            )
            return None
        return model_size

    def set_language(self, info):
        """
        Updates the language attribute based on the detected language information.

        Args:
            info (object): An object containing the detected language and its probability. This object
                        must have at least two attributes: `language`, a string indicating the detected
                        language, and `language_probability`, a float representing the confidence level
                        of the language detection.
        """
        if info.language_probability > 0.5:
            self.language = info.language
            logging.info(f"Detected language {self.language} with probability {info.language_probability}")
            
            language_data = {
                "uid": self.client_uid, 
                "language": self.language, 
                "language_prob": info.language_probability
            }
            self.websocket.send(json.dumps(language_data))
            
            # Log the language detection to file in a more readable format
            logger.info(f"LANGUAGE_DETECTION: client={self.client_uid}, language={self.language}, confidence={info.language_probability:.4f}")

    def transcribe_audio(self, input_sample):
        """
        Transcribes the provided audio sample using the configured transcriber instance.

        If the language has not been set, it updates the session's language based on the transcription
        information.

        Args:
            input_sample (np.array): The audio chunk to be transcribed. This should be a NumPy
                                    array representing the audio data.

        Returns:
            The transcription result from the transcriber. The exact format of this result
            depends on the implementation of the `transcriber.transcribe` method but typically
            includes the transcribed text.
        """
        if ServeClientFasterWhisper.SINGLE_MODEL:
            ServeClientFasterWhisper.SINGLE_MODEL_LOCK.acquire()
        result, info = self.transcriber.transcribe(
            input_sample,
            initial_prompt=self.initial_prompt,
            language=self.language,
            task=self.task,
            vad_filter=self.use_vad,
            vad_parameters=self.vad_parameters if self.use_vad else None)
        if ServeClientFasterWhisper.SINGLE_MODEL:
            ServeClientFasterWhisper.SINGLE_MODEL_LOCK.release()

        if self.language is None and info is not None:
            self.set_language(info)
        return result

    def get_previous_output(self):
        """
        Retrieves previously generated transcription outputs if no new transcription is available
        from the current audio chunks.

        Checks the time since the last transcription output and, if it is within a specified
        threshold, returns the most recent segments of transcribed text. It also manages
        adding a pause (blank segment) to indicate a significant gap in speech based on a defined
        threshold.

        Returns:
            segments (list): A list of transcription segments. This may include the most recent
                            transcribed text segments or a blank segment to indicate a pause
                            in speech.
        """
        segments = []
        if self.t_start is None:
            self.t_start = time.time()
        if time.time() - self.t_start < self.show_prev_out_thresh:
            segments = self.prepare_segments()

        # add a blank if there is no speech for 3 seconds
        if len(self.text) and self.text[-1] != '':
            if time.time() - self.t_start > self.add_pause_thresh:
                self.text.append('')
        return segments

    def handle_transcription_output(self, result, duration):
        """
        Handle the transcription output, updating the transcript and sending data to the client.

        Args:
            result (str): The result from whisper inference i.e. the list of segments.
            duration (float): Duration of the transcribed audio chunk.
        """
        segments = []
        if len(result):
            self.t_start = None
            last_segment = self.update_segments(result, duration)
            segments = self.prepare_segments(last_segment)
        else:
            # show previous output if there is pause i.e. no output from whisper
            segments = self.get_previous_output()

        if len(segments):
            self.send_transcription_to_client(segments)

    def speech_to_text(self):
        """
        Process an audio stream in an infinite loop, continuously transcribing the speech.

        This method continuously receives audio frames, performs real-time transcription, and sends
        transcribed segments to the client via a WebSocket connection.

        If the client's language is not detected, it waits for 30 seconds of audio input to make a language prediction.
        It utilizes the Whisper ASR model to transcribe the audio, continuously processing and streaming results. Segments
        are sent to the client in real-time, and a history of segments is maintained to provide context.Pauses in speech
        (no output from Whisper) are handled by showing the previous output for a set duration. A blank segment is added if
        there is no speech for a specified duration to indicate a pause.

        Raises:
            Exception: If there is an issue with audio processing or WebSocket communication.

        """
        while True:
            if self.exit:
                logging.info("Exiting speech to text thread")
                break

            if self.frames_np is None:
                continue

            self.clip_audio_if_no_valid_segment()

            input_bytes, duration = self.get_audio_chunk_for_processing()
            if duration < 1.0:
                time.sleep(0.1)     # wait for audio chunks to arrive
                continue
            try:
                input_sample = input_bytes.copy()
                result = self.transcribe_audio(input_sample)

                if result is None or self.language is None:
                    self.timestamp_offset += duration
                    time.sleep(0.25)    # wait for voice activity, result is None when no voice activity
                    continue
                self.handle_transcription_output(result, duration)

            except Exception as e:
                logging.error(f"[ERROR]: Failed to transcribe audio chunk: {e}")
                time.sleep(0.01)

    def format_segment(self, start, end, text, completed=False):
        """
        Formats a transcription segment with precise start and end times alongside the transcribed text.

        Args:
            start (float): The start time of the transcription segment in seconds.
            end (float): The end time of the transcription segment in seconds.
            text (str): The transcribed text corresponding to the segment.
            completed (bool): Whether this segment is marked as completed

        Returns:
            dict: A dictionary representing the formatted transcription segment, including
                'start' and 'end' times as strings with three decimal places and the 'text'
                of the transcription.
        """
        # Get the speaker active during this segment
        speaker_id, speaker_name = self.get_speaker_at_time(start, end)
        
        return {
            'start': "{:.3f}".format(start),
            'end': "{:.3f}".format(end),
            'text': text,
            'completed': completed,
            'speaker_id': speaker_id,
            'speaker_name': speaker_name
        }

    def update_segments(self, segments, duration):
        """
        Processes the segments from whisper. Appends all the segments to the list
        except for the last segment assuming that it is incomplete.

        Updates the ongoing transcript with transcribed segments, including their start and end times.
        Complete segments are appended to the transcript in chronological order. Incomplete segments
        (assumed to be the last one) are processed to identify repeated content. If the same incomplete
        segment is seen multiple times, it updates the offset and appends the segment to the transcript.
        A threshold is used to detect repeated content and ensure it is only included once in the transcript.
        The timestamp offset is updated based on the duration of processed segments. The method returns the
        last processed segment, allowing it to be sent to the client for real-time updates.

        Args:
            segments(dict) : dictionary of segments as returned by whisper
            duration(float): duration of the current chunk

        Returns:
            dict or None: The last processed segment with its start time, end time, and transcribed text.
                     Returns None if there are no valid segments to process.
        """
        offset = None
        self.current_out = ''
        last_segment = None

        # process complete segments
        if len(segments) > 1 and segments[-1].no_speech_prob <= self.no_speech_thresh:
            for i, s in enumerate(segments[:-1]):
                text_ = s.text
                self.text.append(text_)
                with self.lock:
                    start, end = self.timestamp_offset + s.start, self.timestamp_offset + min(duration, s.end)

                if start >= end:
                    continue
                if s.no_speech_prob > self.no_speech_thresh:
                    continue

                self.transcript.append(self.format_segment(start, end, text_, completed=True))
                offset = min(duration, s.end)

        # only process the last segment if it satisfies the no_speech_thresh
        if segments[-1].no_speech_prob <= self.no_speech_thresh:
            self.current_out += segments[-1].text
            with self.lock:
                last_segment = self.format_segment(
                    self.timestamp_offset + segments[-1].start,
                    self.timestamp_offset + min(duration, segments[-1].end),
                    self.current_out,
                    completed=False
                )

        if self.current_out.strip() == self.prev_out.strip() and self.current_out != '':
            self.same_output_count += 1

            # if we remove the audio because of same output on the nth reptition we might remove the 
            # audio thats not yet transcribed so, capturing the time when it was repeated for the first time
            if self.end_time_for_same_output is None:
                self.end_time_for_same_output = segments[-1].end
            time.sleep(0.1)     # wait for some voice activity just in case there is an unitended pause from the speaker for better punctuations.
        else:
            self.same_output_count = 0
            self.end_time_for_same_output = None

        # if same incomplete segment is seen multiple times then update the offset
        # and append the segment to the list
        if self.same_output_count > self.same_output_threshold:
            if not len(self.text) or self.text[-1].strip().lower() != self.current_out.strip().lower():
                self.text.append(self.current_out)
                with self.lock:
                    self.transcript.append(self.format_segment(
                        self.timestamp_offset,
                        self.timestamp_offset + min(duration, self.end_time_for_same_output),
                        self.current_out,
                        completed=True
                    ))
            self.current_out = ''
            offset = min(duration, self.end_time_for_same_output)
            self.same_output_count = 0
            last_segment = None
            self.end_time_for_same_output = None
        else:
            self.prev_out = self.current_out

        # update offset
        if offset is not None:
            with self.lock:
                self.timestamp_offset += offset

        return last_segment

# Add the missing TranscriptionBuffer class
class TranscriptionBuffer:
    """Manages buffers of transcription segments for a client"""
    
    def __init__(self, client_uid):
        """Initialize with client ID"""
        self.client_uid = client_uid
        self.partial_segments = []
        self.completed_segments = []
        self.max_segments = 50  # Max number of segments to keep in history
        
    def add_segments(self, partial_segments, completed_segments):
        """Add new segments to the appropriate buffers"""
        if partial_segments:
            self.partial_segments = partial_segments
            
        if completed_segments:
            # Add new completed segments
            self.completed_segments.extend(completed_segments)
            # Trim if exceeding max size
            if len(self.completed_segments) > self.max_segments:
                self.completed_segments = self.completed_segments[-self.max_segments:]
    
    def get_segments_for_response(self):
        """Get formatted segments for client response"""
        # Return completed segments plus any partial segments
        result = []
        
        # Add completed segments
        if self.completed_segments:
            result.extend(self.completed_segments)
            
        # Add partial segments
        if self.partial_segments:
            result.extend(self.partial_segments)
            
        return result
