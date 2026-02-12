import os
import time
import threading
import json
import functools
import logging
import hashlib
import tempfile
from pathlib import Path
from enum import Enum
from typing import List, Optional
import datetime
import websocket
import sys # Added sys import
import socket  # Added to resolve container IP for ws_url

import torch
import numpy as np
from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed
from whisper_live.vad import VoiceActivityDetector
from whisper_live.transcriber import WhisperModel
try:
    from whisper_live.transcriber_tensorrt import WhisperTRTLLM
    TENSORRT_AVAILABLE = True
except Exception:
    TENSORRT_AVAILABLE = False
    WhisperTRTLLM = None

try:
    from whisper_live.remote_transcriber import RemoteTranscriber, RemoteTranscriberOverloaded
    REMOTE_AVAILABLE = True
except Exception:
    REMOTE_AVAILABLE = False
    RemoteTranscriber = None
    RemoteTranscriberOverloaded = None

# Import for health check HTTP server
import http.server
import socketserver
import threading

# Import Redis
import redis
import uuid
import httpx

# Setup basic logging (env-driven)
_WL_LOG_LEVEL = os.getenv("WL_LOG_LEVEL", "INFO").strip().upper()
try:
    logging.basicConfig(level=getattr(logging, _WL_LOG_LEVEL, logging.INFO))
except Exception:
    logging.basicConfig(level=logging.INFO)

# Env-driven logging flags
_def_bool = lambda v: str(v).strip().lower() in ("1", "true", "yes", "on")
WL_LOG_TRANSCRIPTS = _def_bool(os.getenv("WL_LOG_TRANSCRIPTS", "false"))
WL_LOG_TRANSCRIPT_SUMMARY = _def_bool(os.getenv("WL_LOG_TRANSCRIPT_SUMMARY", "true"))
WL_LOG_HALLUCINATIONS = _def_bool(os.getenv("WL_LOG_HALLUCINATIONS", "false"))
WL_LOG_CONTROL_EVENTS = _def_bool(os.getenv("WL_LOG_CONTROL_EVENTS", "false"))
WL_LOG_SPEAKER_EVENTS = _def_bool(os.getenv("WL_LOG_SPEAKER_EVENTS", "false"))
WL_LOG_SPEAKER_PUBLISH = _def_bool(os.getenv("WL_LOG_SPEAKER_PUBLISH", "false"))

# Suppress external chatter
_FW_LEVEL = os.getenv("WL_FAST_WHISPER_LOG_LEVEL", "WARNING").strip().upper()
try:
    logging.getLogger("faster_whisper").setLevel(getattr(logging, _FW_LEVEL, logging.WARNING))
except Exception:
    pass

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
        logging.info(f"TranscriptionCollectorClient instance creating with Redis URL: {self.redis_url}")
        
        self.redis_client = None
        self.is_connected = False
        self.connection_lock = threading.Lock()
        self.connection_thread = None
        self.stop_requested = False
        # Optional back-reference to the TranscriptionServer (set by server after creation)
        self.server_ref = None
        
        # Stream key for transcriptions
        self.stream_key = os.getenv("REDIS_STREAM_KEY", "transcription_segments")
        
        # Stream key for speaker events (NEW)
        self.speaker_events_stream_key = os.getenv("REDIS_SPEAKER_EVENTS_RELATIVE_STREAM_KEY", "speaker_events_relative")
        
        # Track session_uids for which we've published session_start events
        self.session_starts_published = set()

        # Dedupe repeated identical transcript payloads per session_uid.
        # This prevents Redis stream spam and downstream lag (which looks like a "stuck" pipeline).
        self._last_transcription_digest_by_uid = {}
        self._last_transcription_digest_lock = threading.Lock()
        
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

    def publish_speaker_event(self, event_data: dict):
        """Publish a speaker_activity event to the new Redis stream.
        
        Args:
            event_data: The payload from the Vexa Bot's speaker_activity message.
                        This includes uid, relative_client_timestamp_ms, participant_name, etc.
        
        Returns:
            Boolean indicating success or failure
        """
        if not self.is_connected or not self.redis_client:
            logging.warning(f"Cannot publish speaker event to {self.speaker_events_stream_key}: Not connected to Redis")
            return False
            
        if not event_data or not isinstance(event_data, dict):
            logging.error(f"Invalid event_data for publishing to {self.speaker_events_stream_key}")
            return False

        try:
            # Add server received timestamp
            now = datetime.datetime.utcnow()
            timestamp_iso = now.isoformat() + "Z"
            
            # Create a new dictionary for the Redis message to avoid modifying the original
            redis_message_payload = event_data.copy()
            redis_message_payload["server_received_timestamp_iso"] = timestamp_iso
            
            # Ensure all values in redis_message_payload are suitable for xadd
            # (typically strings, numbers, or booleans)
            # For simplicity, we assume the structure is already flat as per planstate.md
            
            result = self.redis_client.xadd(
                self.speaker_events_stream_key,
                redis_message_payload 
            )
            
            if result:
                if WL_LOG_SPEAKER_PUBLISH:
                    uid = redis_message_payload.get('uid', 'N/A')
                    event_type = redis_message_payload.get('event_type', 'N/A')
                    logging.info(f"Published speaker event ({event_type}) for UID {uid} to {self.speaker_events_stream_key}")
                return True
            else:
                uid = redis_message_payload.get('uid', 'N/A')
                logging.error(f"Failed to publish speaker event for UID {uid} to {self.speaker_events_stream_key}")
                return False
                
        except Exception as e:
            uid = event_data.get('uid', 'N/A')
            logging.error(f"Error publishing speaker event for UID {uid} to {self.speaker_events_stream_key}: {e}")
            logging.error(f"Error publishing transcription: {e}")
            return False

    def publish_session_end_event(self, token, platform, meeting_id, session_uid):
        # ... (This method was in the original TranscriptionCollectorClient, ensure it's still there and correct)
        # For brevity, not re-listing its full content if unchanged by this specific Phase 2 task.
        # It should publish a message like: 
        # payload = {
        #     "type": "session_end",
        #     "token": token, 
        #     "platform": platform, 
        #     "meeting_id": meeting_id, 
        #     "uid": session_uid,
        #     "end_timestamp": timestamp_iso 
        # }
        # to self.stream_key (transcription_segments stream)
        if not self.is_connected or not self.redis_client:
            logging.warning(f"Cannot publish session_end for UID {session_uid}: Not connected to Redis")
            return False
        try:
            now = datetime.datetime.utcnow()
            timestamp_iso = now.isoformat() + "Z"
            payload = {
                "type": "session_end",
                "token": token,
                "platform": platform,
                "meeting_id": meeting_id,
                "uid": session_uid,
                "end_timestamp": timestamp_iso
            }
            message = {"payload": json.dumps(payload)}
            result = self.redis_client.xadd(self.stream_key, message)
            if result:
                logging.info(f"Published session_end event for UID {session_uid} to {self.stream_key}")
                # Remove from published starts if present, as session is now considered ended
                if session_uid in self.session_starts_published:
                    self.session_starts_published.remove(session_uid)
                # Clear dedupe state for this session
                try:
                    with self._last_transcription_digest_lock:
                        self._last_transcription_digest_by_uid.pop(session_uid, None)
                except Exception:
                    pass
                return True
            else:
                logging.error(f"Failed to publish session_end for UID {session_uid} to {self.stream_key}")
                return False
        except Exception as e:
            logging.error(f"Error publishing session_end for UID {session_uid} to {self.stream_key}: {e}")
            return False

    def send_transcription(self, token, platform, meeting_id, segments, session_uid=None):
        """Send transcription segments to Redis stream (self.stream_key).
        
        Args:
            token: User's API token
            platform: Platform identifier (e.g., 'google_meet') 
            meeting_id: Platform-specific meeting ID
            segments: List of transcription segments
            session_uid: Optional unique identifier for this session
            
        Returns:
            Boolean indicating success or failure
        """
        if not self.is_connected or not self.redis_client:
            logging.warning(f"Cannot send transcription to {self.stream_key}: Not connected to Redis")
            return False
            
        # segments can be an empty list (e.g. for an early session_end or empty audio), 
        # but other fields are required
        if not all([token, platform, meeting_id]): 
            logging.error(f"Missing required fields (token, platform, or meeting_id) for transcription UID {session_uid}")
            return False
            
        if not session_uid:
            # This case should ideally be rare if uid is managed by the caller (ServeClient)
            logging.warning("session_uid not provided to send_transcription, generating one.")
            session_uid = str(uuid.uuid4())
            
        # If this is the first time we're seeing this session_uid for transcriptions, 
        # publish a session_start event.
        if session_uid not in self.session_starts_published:
            self.publish_session_start_event(token, platform, meeting_id, session_uid)
        
        try:
            payload = {
                "type": "transcription", 
                "token": token,
                "platform": platform, 
                "meeting_id": meeting_id,
                "segments": segments, 
                "uid": session_uid
            }

            # Dedupe identical payloads per session_uid (skip publish if unchanged)
            payload_json = None
            try:
                payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha1(payload_json.encode("utf-8")).hexdigest()
                with self._last_transcription_digest_lock:
                    prev = self._last_transcription_digest_by_uid.get(session_uid)
                    if prev == digest:
                        return True
                    self._last_transcription_digest_by_uid[session_uid] = digest
            except Exception:
                payload_json = None

            message = {
                # Per current structure, the whole payload is JSON dumped into one field
                "payload": payload_json or json.dumps(payload)
            }
            
            result = self.redis_client.xadd(
                self.stream_key, 
                message
            )
            
            if result:
                logging.debug(f"Published transcription with {len(segments)} segments for UID {session_uid} to {self.stream_key}")
                return True
            else:
                logging.error(f"Failed to publish transcription for UID {session_uid} to {self.stream_key}")
                return False
                
        except Exception as e:
            logging.error(f"Error publishing transcription for UID {session_uid} to {self.stream_key}: {e}")
            return False

class ClientManager:
    def __init__(self, max_clients=4, max_connection_time=3600):
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
    REMOTE = "remote"

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

    def is_remote(self) -> bool:
        return self == BackendType.REMOTE


class TranscriptionServer:
    RATE = 16000

    def __init__(self):
        self.client_manager = None
        self.no_voice_activity_chunks = 0
        self.use_vad = True
        self.single_model = False
        
        # Instantiate TranscriptionCollectorClient here
        self.collector_client: Optional[TranscriptionCollectorClient] = None
        redis_stream_url_env = os.getenv("REDIS_STREAM_URL")
        if redis_stream_url_env:
            self.collector_client = TranscriptionCollectorClient(redis_stream_url=redis_stream_url_env)
            try:
                # Attach back-reference so client handlers can update server_last_transcription_ts
                self.collector_client.server_ref = self
            except Exception:
                pass
            # Attempt to connect the collector client immediately if needed, or rely on its internal connect()
            if hasattr(self.collector_client, 'connect') and callable(getattr(self.collector_client, 'connect')) and not self.collector_client.is_connected:
                 # This connect call is from the original global init, ensuring it's still triggered
                 # if TranscriptionCollectorClient's own __init__ doesn't auto-connect fully.
                 # Based on its code, __init__ calls self.connect() which starts a thread.
                 pass # self.collector_client.connect() is called in its __init__
        else:
            logging.warning("REDIS_STREAM_URL not set. TranscriptionCollectorClient will not be initialized in TranscriptionServer.")

        self.is_healthy = False  # Represents WebSocket server readiness primarily
        self.health_server = None
        self.backend = None # Initialize backend attribute

        # Self-monitoring
        self.unhealthy_streak = 0
        self.max_unhealthy_streak = 5  # Exit after 5 consecutive failed health checks
        self.health_monitor_interval = 30  # Check health every 30 seconds
        self.self_monitor_thread = None
        self._stop_self_monitor = threading.Event()

        # --- Server-level speaker-based circuit breaker configuration ---
        # Use speaker activity as ground truth for "speech happening".
        def _get_bool_env(name: str, default: str) -> bool:
            val = os.getenv(name, default).strip().lower()
            return val in ("1", "true", "yes", "on")

        # Master enable/disable flag for circuit breaker (default: disabled)
        self.circuit_breaker_enabled = _get_bool_env("WL_CIRCUIT_BREAKER_ENABLED", "false")

        self.use_speaker_ground_truth = _get_bool_env("WL_USE_SPEAKER_GROUND_TRUTH", "true")
        try:
            self.server_speaker_no_tx_stall_s = float(os.getenv("WL_SERVER_SPEAKER_NO_TX_STALL_S", "30"))
        except Exception:
            self.server_speaker_no_tx_stall_s = 30.0
        try:
            self.speaker_active_window_s = float(os.getenv("WL_SPEAKER_ACTIVE_WINDOW_S", "8"))
        except Exception:
            self.speaker_active_window_s = 8.0
        try:
            self.server_warmup_s = float(os.getenv("WL_SERVER_WARMUP_S", "60"))
        except Exception:
            self.server_warmup_s = 60.0

        # Timestamps tracked globally across all sessions
        self.server_start_ts = time.time()
        self.server_last_transcription_ts = None  # updated whenever any session emits segments
        self.last_speaker_event_ts = None         # updated on incoming speaker_activity events

        # Circuit breaker consecutive trigger requirement (avoid single-check flaps)
        try:
            self.circuit_breaker_consecutive = int(os.getenv("WL_CIRCUIT_BREAKER_CONSECUTIVE", "2"))
        except Exception:
            self.circuit_breaker_consecutive = 2
        self.no_tx_while_speaker_streak = 0
        logging.info(
            f"CONFIG: speaker_circuit_breaker use_speaker_gt={self.use_speaker_ground_truth}, "
            f"stall={self.server_speaker_no_tx_stall_s}s, speaker_window={self.speaker_active_window_s}s, warmup={self.server_warmup_s}s"
        )

        # --- Capacity configuration (WL_MAX_CLIENTS) ---
        # Will be set in run() method based on backend type
        self.config_max_clients = 10  # Default, will be overridden in run() if remote

        # --- WL discovery / addressing ---
        self._wl_redis = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
        self._listen_port = int(os.getenv("WL_LISTEN_PORT", os.getenv("PORT", "9090")))
        # Prefer Nomad alloc-id for stable grouping; fall back to HOSTNAME or random uuid
        self._alloc_id = os.getenv("NOMAD_ALLOC_ID", os.getenv("HOSTNAME", str(uuid.uuid4())[:8]))
        
        # Use forced IP from environment if available, otherwise derive container IP
        forced_ip = os.getenv("WL_FORCE_IP")
        if forced_ip:
            self._pod_ip = forced_ip
            logging.info(f"✅ USING FORCED IP: WL_FORCE_IP={forced_ip}")
        else:
            # Derive container IP on the same network used to reach Redis (guaranteed shared with other app services).
            try:
                probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # UDP connect does not send packets; it just sets internal routing.
                probe_sock.connect((os.getenv("REDIS_HOST", "redis"), int(os.getenv("REDIS_PORT", "6379"))))
                self._pod_ip = probe_sock.getsockname()[0]
                probe_sock.close()
            except Exception:
                # Fallback to hostname resolution
                try:
                    self._pod_ip = socket.gethostbyname(socket.gethostname())
                except Exception:
                    self._pod_ip = "127.0.0.1"
            logging.info(f"⚠️  AUTO-DETECTED IP: {self._pod_ip} (no WL_FORCE_IP set)")
        
        logging.info(f"🔍 FINAL POD IP: {self._pod_ip}")
        logging.info(f"🔍 LISTEN PORT: {self._listen_port}")
        logging.info(f"🔍 ENV WL_FORCE_IP: {os.getenv('WL_FORCE_IP', 'NOT_SET')}")
        logging.info(f"🔍 ENV WL_LISTEN_PORT: {os.getenv('WL_LISTEN_PORT', 'NOT_SET')}")
        
        self._ws_url = f"ws://{self._pod_ip}:{self._listen_port}/ws"
        logging.info(f"🌐 WEBSOCKET URL CONFIGURED: {self._ws_url}")
        logging.info(f"🌐 WhisperLive WebSocket URL: {self._ws_url}")
        self._metric_stop_evt = threading.Event()
        
        # Initialize Consul configuration
        self._consul_enabled = os.getenv("CONSUL_ENABLE", "false").strip().lower() in ("1", "true", "yes", "on")
        if self._consul_enabled:
            self._consul_http_addr = os.getenv("CONSUL_HTTP_ADDR", "http://consul:8500")
            # Make service ID stable per ip:port to avoid duplicates across restarts
            safe_ip = self._pod_ip.replace('.', '-')
            self._consul_service_id = f"whisperlive-{safe_ip}-{self._listen_port}"
            logging.info(f"🔍 CONSUL ENABLED: {self._consul_http_addr}, service_id={self._consul_service_id}")
        # Register OS signal handlers to gracefully deregister on shutdown
        try:
            self._register_signal_handlers()
        except Exception as exc:
            logging.warning(f"Failed to register shutdown handlers: {exc}")
        # --- End WL Scaling block ---

    # --- Connection cleanup helper methods ---
    def _cleanup_stale_connections(self):
        """Remove stale WebSocket connections that are no longer active."""
        if not self.client_manager:
            return
        
        stale_websockets = []
        for websocket in list(self.client_manager.clients.keys()):
            try:
                # Check if websocket is still open
                if hasattr(websocket, 'closed') and websocket.closed:
                    stale_websockets.append(websocket)
                    continue
                    
                # Check connection timeout
                if self.client_manager.is_client_timeout(websocket):
                    stale_websockets.append(websocket)
                    continue
                    
            except Exception as e:
                logging.warning(f"Error checking websocket health, marking as stale: {e}")
                stale_websockets.append(websocket)
        
        # Remove stale connections
        removed_count = 0
        for websocket in stale_websockets:
            try:
                client = self.client_manager.clients.get(websocket)
                client_uid = client.client_uid if client else 'unknown'
                logging.info(f"Removing stale connection: {client_uid}")
                self.client_manager.remove_client(websocket)
                removed_count += 1
            except Exception as e:
                logging.warning(f"Error removing stale connection: {e}")
        
        if removed_count > 0:
            logging.info(f"Cleaned up {removed_count} stale connections")

    def _periodic_cleanup(self):
        """Periodically clean up stale connections every 30 seconds."""
        while not self._metric_stop_evt.is_set():
            try:
                self._cleanup_stale_connections()
            except Exception as e:
                logging.warning(f"Error in periodic cleanup: {e}")
            self._metric_stop_evt.wait(30)  # Check every 30 seconds
    # --- End connection cleanup methods ---

    def _register_signal_handlers(self):
        import signal
        def _handler(signum, frame):
            try:
                self._on_shutdown(signum)
            finally:
                # Best-effort immediate process exit after cleanup
                pass
        # Register common termination signals
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)

    def _on_shutdown(self, signum):
        """Gracefully clean up connections and deregister from Consul."""
        try:
            self._metric_stop_evt.set()
        except Exception:
            pass
        
        # Clean up any remaining connections
        try:
            if self.client_manager:
                remaining_clients = len(self.client_manager.clients)
                if remaining_clients > 0:
                    logging.info(f"Cleaning up {remaining_clients} remaining connections on shutdown")
                    for websocket in list(self.client_manager.clients.keys()):
                        try:
                            self.client_manager.remove_client(websocket)
                        except Exception as e:
                            logging.warning(f"Error cleaning up connection on shutdown: {e}")
        except Exception as exc:
            logging.warning(f"Failed to clean up connections on shutdown: {exc}")

    def initialize_client(
        self, websocket, options, faster_whisper_custom_model_path,
        whisper_tensorrt_path, trt_multilingual
    ):
        """
        Initializes a client based on the backend type.
        """
        if options is None:
            options = {}
        transcription_tier = str(options.get("transcription_tier", "realtime")).strip().lower()
        if transcription_tier not in ("realtime", "deferred"):
            transcription_tier = "realtime"
        backend_str = options.get("backend", self.backend)
        backend = BackendType(backend_str)
        
        # tensorrt client
        if backend.is_tensorrt():
            client = ServeClientTensorRT(
                websocket,
                multilingual=self.trt_multilingual,
                language=options.get("language"),
                task=options.get("task", "transcribe"),
                client_uid=options.get("uid"),
                model=self.whisper_tensorrt_path,
                single_model=self.single_model,
                platform=options.get("platform"),
                meeting_url=options.get("meeting_url"),
                token=options.get("token"),
                meeting_id=options.get("meeting_id"),
                transcription_tier=transcription_tier,
                collector_client_ref=self.collector_client,
                server_options=self.server_options
            )
        # remote client
        elif backend.is_remote():
            # Get model from options or env, handling None case
            remote_model = options.get("model") or os.getenv("REMOTE_TRANSCRIBER_MODEL")
            client = ServeClientRemote(
                websocket,
                language=options.get("language"),
                task=options.get("task", "transcribe"),
                client_uid=options.get("uid"),
                model=remote_model,
                initial_prompt=options.get("initial_prompt"),
                vad_parameters=options.get("vad_parameters"),
                use_vad=options.get("use_vad", True),
                platform=options.get("platform"),
                meeting_url=options.get("meeting_url"),
                token=options.get("token"),
                meeting_id=options.get("meeting_id"),
                transcription_tier=transcription_tier,
                collector_client_ref=self.collector_client,
                server_options=self.server_options
            )
        # faster-whisper client
        else:
            client = ServeClientFasterWhisper(
                websocket,
                language=options.get("language"),
                task=options.get("task", "transcribe"),
                client_uid=options.get("uid"),
                model=self.faster_whisper_custom_model_path or options.get("model", "small.en"),
                initial_prompt=options.get("initial_prompt"),
                vad_parameters=options.get("vad_parameters"),
                use_vad=options.get("use_vad", True),
                single_model=self.single_model,
                platform=options.get("platform"),
                meeting_url=options.get("meeting_url"),
                token=options.get("token"),
                meeting_id=options.get("meeting_id"),
                transcription_tier=transcription_tier,
                collector_client_ref=self.collector_client,
                server_options=self.server_options
            )
        self.client_manager.add_client(websocket, client)
        logging.info(f"Added client {client.client_uid}, total clients: {len(self.client_manager.clients)}")

    def get_audio_from_websocket(self, websocket):
        """
        Receives audio buffer from websocket and creates a numpy array out of it.
        Also handles JSON control messages (speaker events, session control).

        Args:
            websocket: The websocket to receive audio from.

        Returns:
            A numpy array containing the audio, or False if END_OF_AUDIO, or None if control message processed.
        """
        frame_data = websocket.recv()
        
        # Handle END_OF_AUDIO signal
        if frame_data == b"END_OF_AUDIO":
            return False
            
        # Check if this is a JSON control message (string) or binary audio data
        try:
            # Try to decode as JSON string first
            if isinstance(frame_data, str) or (isinstance(frame_data, bytes) and frame_data.startswith(b'{')):
                # This is a JSON control message
                if isinstance(frame_data, bytes):
                    frame_data = frame_data.decode('utf-8')
                
                control_message = json.loads(frame_data)
                message_type = control_message.get("type", "unknown")
                
                if WL_LOG_CONTROL_EVENTS:
                    logging.info(f"Received control message type: {message_type}")
                
                if message_type == "speaker_activity":
                    # CORRECTED DISPATCH: Route "speaker_activity" to the new handler
                    self.handle_speaker_activity_update(websocket, control_message)
                elif message_type == "speaker_activity_update":
                    # This branch can remain if "speaker_activity_update" is a distinct, valid type for other purposes.
                    # Otherwise, it could be removed if "speaker_activity" is the sole type for this data.
                    # For now, keeping it to ensure no other functionality breaks, assuming it might be used.
                    self.handle_speaker_activity_update(websocket, control_message)
                elif message_type == "audio_chunk_metadata":
                    self.handle_audio_chunk_metadata(websocket, control_message)
                elif message_type == "session_control":
                    self.handle_session_control(websocket, control_message)
                else:
                    logging.warning(f"Unknown control message type: {message_type}")
                
                # Return None to indicate control message was processed (not audio)
                return None
                
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Not a JSON message, treat as binary audio data
            pass
        
        # Process as binary audio data
        try:
            return np.frombuffer(frame_data, dtype=np.float32)
        except (ValueError, TypeError) as e:
            logging.error(f"Failed to process audio data: {e}")
            return None

    def handle_speaker_event(self, websocket, control_message):
        """
        Handle speaker activity events from the bot.
        
        Args:
            websocket: The websocket connection
            control_message: The parsed speaker event message
        """
        try:
            payload = control_message.get("payload", {})
            event_type = payload.get("event_type")
            participant_name = payload.get("participant_name")
            participant_id = payload.get("participant_id_meet")
            timestamp = payload.get("client_timestamp_ms")
            
            logging.info(f"Speaker Event: {event_type} - {participant_name} ({participant_id}) at {timestamp}")
            
            # Future Phase 2: Store speaker events for timeline correlation
            # For now, just log the events
            
        except Exception as e:
            logging.error(f"Error processing speaker event: {e}")

    def handle_session_control(self, websocket, control_message):
        """
        Handle session control messages from the bot.
        
        Args:
            websocket: The websocket connection
            control_message: The parsed session control message
        """
        try:
            payload = control_message.get("payload", {})
            event = payload.get("event")
            session_uid = payload.get("uid")
            timestamp = payload.get("client_timestamp_ms")
            
            logging.info(f"Session Control: {event} - Session {session_uid} at {timestamp}")
            
            if event == "LEAVING_MEETING":
                # Handle graceful disconnect
                logging.info(f"Bot signaled LEAVING_MEETING for session {session_uid}")
                # The connection will be closed by the bot, we just acknowledge
                
        except Exception as e:
            logging.error(f"Error processing session control: {e}")

    def handle_speaker_activity_update(self, websocket, control_message):
        """
        Handle speaker activity update messages from the bot.
        These are additional speaker state updates beyond the main speaker_activity events.
        
        Args:
            websocket: The websocket connection
            control_message: The parsed speaker activity update message
        """
        try:
            payload = control_message.get("payload", {})
            logging.debug(f"Speaker Activity Update received: {payload}")
            
            # Future Phase 2: Could be used for additional speaker state tracking
            # For now, just log at debug level to avoid cluttering logs
            
        except Exception as e:
            logging.error(f"Error processing speaker activity update: {e}")

    def handle_audio_chunk_metadata(self, websocket, control_message):
        """
        Handle audio chunk metadata messages from the bot.
        These contain information about audio chunks being processed.
        
        Args:
            websocket: The websocket connection
            control_message: The parsed audio chunk metadata message
        """
        try:
            payload = control_message.get("payload", {})
            logging.debug(f"Audio Chunk Metadata received: {payload}")
            
            # Future Phase 2: Could be used for audio quality monitoring, chunk timing analysis, etc.
            # For now, just log at debug level to avoid cluttering logs
            
        except Exception as e:
            logging.error(f"Error processing audio chunk metadata: {e}")

    def handle_new_connection(self, websocket, faster_whisper_custom_model_path,
                              whisper_tensorrt_path, trt_multilingual):
        try:
            logging.info("New client connected")
            options = websocket.recv()
            logging.info(f"Received raw message from client: {options}")
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
                # Enforce server-side capacity from env (ignore client-provided max_clients)
                max_clients = int(self.config_max_clients)
                max_connection_time = options.get('max_connection_time', 3600)
                self.client_manager = ClientManager(max_clients, max_connection_time)
                logging.info(f"CAPACITY: Initialized ClientManager with max_clients={max_clients}, max_connection_time={max_connection_time}")

            self.use_vad = options.get('use_vad')
            if self.client_manager.is_server_full(websocket, options):
                websocket.close()
                return False  # Indicates that the connection should not continue

            if self.backend and self.backend.is_tensorrt(): # Check if self.backend is not None
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
        frame_np = self.get_audio_from_websocket(websocket)
        client = self.client_manager.get_client(websocket)
        
        # Handle different return values from get_audio_from_websocket
        if frame_np is False:
            # END_OF_AUDIO received
            if self.backend.is_tensorrt():
                client.set_eos(True)
            return False
        elif frame_np is None:
            # Control message processed or error occurred, continue processing
            return True

        if self.backend.is_tensorrt():
            voice_active = self.voice_activity(websocket, frame_np)
            if voice_active:
                self.no_voice_activity_chunks = 0
                client.set_eos(False)
            if self.use_vad and not voice_active:
                return True

        client.add_frames(frame_np)
        return True

    def recv_audio(self,
                   websocket,
                   backend: BackendType = BackendType.FASTER_WHISPER,
                   faster_whisper_custom_model_path=None,
                   whisper_tensorrt_path=None,
                   trt_multilingual=False):
        self.backend = backend # Set the backend for the TranscriptionServer instance
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
            port=9090,  # Unified port for both GPU and CPU versions
            backend="tensorrt",
            faster_whisper_custom_model_path=None,
            whisper_tensorrt_path=None,
            trt_multilingual=False,
            single_model=False,
            server_options=None):
        """
        Run the transcription server.
        """
        self.backend = BackendType(backend)
        self.faster_whisper_custom_model_path = faster_whisper_custom_model_path
        self.whisper_tensorrt_path = whisper_tensorrt_path
        self.trt_multilingual = trt_multilingual
        self.single_model = single_model
        self.server_options = server_options or {}
        
        # Set max_clients based on backend type
        if self.backend.is_remote():
            # Remote mode always uses 1000 max clients (hardcoded for scalability)
            self.config_max_clients = 1000
            logging.info("CONFIG: max_clients=1000 (hardcoded for remote backend)")
        else:
            try:
                self.config_max_clients = int(os.getenv("WL_MAX_CLIENTS", "10"))
            except Exception:
                self.config_max_clients = 10
            logging.info(f"CONFIG: max_clients={self.config_max_clients}")

        # For the health check, we need to know if Redis is being used.
        # This is inferred from the presence of the REDIS_STREAM_URL env var.
        redis_url_for_health_check = os.getenv("REDIS_STREAM_URL")
        if redis_url_for_health_check:
            self.start_health_check_server(host, 9091)

        logger.info(f"SERVER_START: host={host}, port={port}, backend={self.backend.value}, single_model={single_model}")
        # Consul self-registration (if enabled)
        try:
            if getattr(self, "_consul_enabled", False):
                self._consul_register_service()
        except Exception as e:
            logging.warning(f"CONSUL_REGISTER failed: {e}")
        
        # Start periodic connection cleanup
        threading.Thread(target=self._periodic_cleanup, daemon=True).start()
        
        with serve(
            functools.partial(
                self.recv_audio,
                backend=self.backend, # Pass the enum member
                faster_whisper_custom_model_path=faster_whisper_custom_model_path,
                whisper_tensorrt_path=whisper_tensorrt_path,
                trt_multilingual=trt_multilingual
            ),
            host,
            port
        ) as server:
            self.is_healthy = True # WebSocket server is up
            logger.info(f"SERVER_RUNNING: WhisperLive server running on {host}:{port} with health check on {host}:9091/health and max_clients={self.config_max_clients}")
            
            # Server started successfully
            logging.info(f"WhisperLive server started successfully on {host}:{port}")
            
            # Start self-monitoring thread
            if self.self_monitor_thread is None:
                self._stop_self_monitor.clear()
                self.self_monitor_thread = threading.Thread(target=self._self_monitor, daemon=True)
                self.self_monitor_thread.start()
                logger.info(f"SELF_MONITOR: Started self-monitoring thread. Interval: {self.health_monitor_interval}s, Max Streak: {self.max_unhealthy_streak}")

            server.serve_forever()

    # --- Consul helpers ---
    def _consul_register_service(self):
        if not getattr(self, "_consul_enabled", False):
            return
        # Before registering, dedupe any older registrations for the same ip:port
        try:
            import urllib.request as _urllib_request
            import json as _json
            with _urllib_request.urlopen(f"{self._consul_http_addr}/v1/agent/services", timeout=3) as resp:
                services = _json.loads(resp.read().decode("utf-8"))
            for sid, s in services.items():
                if s.get("Service") == "whisperlive" and s.get("Address") == self._pod_ip and int(s.get("Port", 0)) == int(self._listen_port) and sid != self._consul_service_id:
                    try:
                        _urllib_request.urlopen(_urllib_request.Request(f"{self._consul_http_addr}/v1/agent/service/deregister/{sid}", method="PUT"), timeout=3)
                        logging.info(f"CONSUL_DEDUP: Deregistered duplicate service {sid} for {self._pod_ip}:{self._listen_port}")
                    except Exception as _e:
                        logging.warning(f"CONSUL_DEDUP failed for {sid}: {_e}")
        except Exception as _e:
            logging.warning(f"CONSUL_DEDUP scan failed: {_e}")
        service_payload = {
            "Name": "whisperlive",
            "ID": self._consul_service_id,
            "Address": self._pod_ip,
            "Port": int(self._listen_port),
          "Tags": [
              "websocket",
              "vexa",
              "traefik.enable=true",
              "traefik.http.routers.whisperlive.rule=PathPrefix(`/ws`)",
              "traefik.http.routers.whisperlive.service=whisperlive",
              f"traefik.http.services.whisperlive.loadbalancer.server.port={self._listen_port}"
          ],
            "Checks": [
                {
                    "Name": "whisperlive-health",
                    "HTTP": f"http://{self._pod_ip}:9091/health",
                    "Interval": "10s",
                    "Timeout": "2s",
                    "DeregisterCriticalServiceAfter": "1m"
                }
            ]
        }
        data = json.dumps(service_payload).encode("utf-8")
        url = f"{self._consul_http_addr}/v1/agent/service/register"
        import urllib.request as _urllib_request
        req = _urllib_request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="PUT")
        with _urllib_request.urlopen(req, timeout=3) as resp:
            if resp.status not in (200, 204):
                raise RuntimeError(f"Consul register HTTP {resp.status}")
        logging.info(f"CONSUL_REGISTERED: {self._consul_service_id} at {self._pod_ip}:{self._listen_port}")

    def _consul_deregister_service(self):
        if not getattr(self, "_consul_enabled", False):
            return
        url = f"{self._consul_http_addr}/v1/agent/service/deregister/{self._consul_service_id}"
        import urllib.request as _urllib_request
        req = _urllib_request.Request(url, method="PUT")
        try:
            with _urllib_request.urlopen(req, timeout=3) as resp:
                if resp.status not in (200, 204):
                    logging.warning(f"CONSUL_DEREGISTER non-2xx: {resp.status}")
        except Exception as e:
            logging.warning(f"CONSUL_DEREGISTER failed: {e}")

    def _self_monitor(self):
        """Periodically checks internal health and exits if persistently unhealthy."""
        while not self._stop_self_monitor.is_set():
            try:
                # Check WebSocket server status (already tracked by self.is_healthy)
                websocket_ok = self.is_healthy

                # Check Redis connection status
                redis_ok = False
                redis_ping_details = "Collector client not initialized or not connected"
                if self.collector_client and self.collector_client.is_connected and self.collector_client.redis_client:
                    try:
                        with self.collector_client.connection_lock:
                            if self.collector_client.redis_client:
                                self.collector_client.redis_client.ping()
                                redis_ok = True
                                redis_ping_details = "Ping OK"
                            else:
                                redis_ping_details = "redis_collector.redis_client is None (within lock)"
                    except redis.exceptions.RedisError as e:
                        redis_ping_details = f"Redis ping failed: {str(e)}"
                        logging.warning(f"Self-monitor: {redis_ping_details}")
                    except Exception as e:
                        redis_ping_details = f"Unexpected error during Redis ping: {str(e)}"
                        logging.warning(f"Self-monitor: {redis_ping_details}")
                elif self.collector_client and not self.collector_client.is_connected:
                    redis_ping_details = "Collector client initialized but not connected to Redis"

                # Server-level stall detection (gated only by master flag)
                if self.circuit_breaker_enabled:
                    now = time.time()
                    # Warmup grace period
                    if (now - self.server_start_ts) < self.server_warmup_s:
                        # During warmup do not evaluate breaker
                        self.no_tx_while_speaker_streak = 0
                    else:
                    # Consider there is current speaking activity if we saw a speaker event recently
                        speaker_active = (
                            self.last_speaker_event_ts is not None and
                            (now - self.last_speaker_event_ts) <= self.speaker_active_window_s
                        )
                        # Only evaluate breaker if core dependencies look OK (avoid tripping while already unhealthy)
                        if websocket_ok and redis_ok and speaker_active:
                            no_tx_age = None
                            if self.server_last_transcription_ts is None:
                                no_tx_age = float('inf')
                            else:
                                no_tx_age = now - self.server_last_transcription_ts

                            if no_tx_age is not None and no_tx_age >= self.server_speaker_no_tx_stall_s:
                                self.no_tx_while_speaker_streak += 1
                                if self.no_tx_while_speaker_streak >= max(1, self.circuit_breaker_consecutive):
                                    logging.critical(
                                        f"WATCHDOG: SERVER_CIRCUIT_TRIPPED after {self.no_tx_while_speaker_streak} consecutive checks; "
                                        f"speaker_active window={self.speaker_active_window_s}s but no transcripts for {no_tx_age:.1f}s "
                                        f"(>= {self.server_speaker_no_tx_stall_s}s). Exiting."
                                    )
                                    self._graceful_shutdown_and_exit()
                                    return
                            else:
                                # Transcripts resumed or not stalled long enough
                                if self.no_tx_while_speaker_streak > 0:
                                    logging.info("WATCHDOG: breaker condition cleared; resetting streak")
                                self.no_tx_while_speaker_streak = 0
                        else:
                            # No speaker activity or dependencies not OK; do not count
                            self.no_tx_while_speaker_streak = 0

                if websocket_ok and redis_ok:
                    if self.unhealthy_streak > 0:
                        logging.info(f"Self-monitor: Service recovered. WebSocket: OK, Redis: OK. Unhealthy streak reset.")
                    self.unhealthy_streak = 0
                else:
                    self.unhealthy_streak += 1
                    logging.warning(
                        f"Self-monitor: Unhealthy check #{self.unhealthy_streak}/{self.max_unhealthy_streak}. "
                        f"WebSocket Ready: {websocket_ok}, Redis Connected: {redis_ok} (Details: {redis_ping_details})"
                    )

                if self.unhealthy_streak >= self.max_unhealthy_streak:
                    logging.critical(
                        f"Self-monitor: Service unhealthy for {self.unhealthy_streak} consecutive checks. "
                        f"Max streak of {self.max_unhealthy_streak} reached. Initiating self-termination."
                    )
                    self._graceful_shutdown_and_exit()
                    return # Exit thread

            except Exception as e:
                # Catch any unexpected errors in the monitoring loop itself
                logging.error(f"Self-monitor: Unexpected error in monitoring loop: {e}", exc_info=True)
                self.unhealthy_streak +=1 # Count this as an unhealthy check to be safe
                if self.unhealthy_streak >= self.max_unhealthy_streak:
                    logging.critical(f"Self-monitor: Exiting due to repeated errors in monitoring loop.")
                    self._graceful_shutdown_and_exit()
                    return # Exit thread
            
            self._stop_self_monitor.wait(self.health_monitor_interval)

    def _graceful_shutdown_and_exit(self):
        """Attempts to gracefully shut down components and then exits the process."""
        logging.info("Self-monitor: Attempting graceful shutdown...")
        
        # 1. Stop accepting new connections / mark as unhealthy for external checks
        self.is_healthy = False

        # 2. Stop the self-monitor thread from looping again
        self._stop_self_monitor.set()

        # 3. Close the HTTP health server
        if self.health_server:
            try:
                logging.info("Self-monitor: Shutting down HTTP health check server...")
                self.health_server.shutdown() # Graceful shutdown
                self.health_server.server_close() # Release port
                logging.info("Self-monitor: HTTP health check server shut down.")
            except Exception as e:
                logging.error(f"Self-monitor: Error shutting down HTTP health_server: {e}", exc_info=True)
        
        # 4. Do NOT proactively disconnect Redis from a background thread.
        #    If we need to self-heal, exit the process and let the supervisor restart cleanly.

        # 5. TODO: Add cleanup for active WebSocket client connections if possible.
        # This is complex as `server.serve_forever()` blocks the main thread.
        # Options: server.shutdown() if available, or rely on process exit for now.

        logging.critical("Self-monitor: Shutdown sequence complete. Forcing process exit with code 1.")
        try:
            import os
            os._exit(1)  # Ensure the whole process terminates even if called from a non-main thread
        except Exception:
            sys.exit(1)

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
        vad_result = self.vad_detector(frame_np)
        if not vad_result:
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
        client = self.client_manager.get_client(websocket)
        if client:
            client_uid = client.client_uid if hasattr(client, 'client_uid') else 'unknown'
            self.client_manager.remove_client(websocket)
            logging.info(f"Removed client {client_uid}, remaining clients: {len(self.client_manager.clients)}")
        else:
            logging.warning("Attempted to cleanup websocket that was not found in client_manager")

    def start_health_check_server(self, host, port):
        """Start a simple HTTP server for health checks.
        
        This runs in a separate thread and listens on a different port than the WebSocket server.
        """
        parent_server_instance = self # This is the TranscriptionServer instance

        class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):
            # Store references passed via functools.partial
            def __init__(self, *args, transcription_server_ref, redis_collector_ref, **kwargs):
                self.transcription_server_instance = transcription_server_ref
                self.redis_collector = redis_collector_ref # This is the TranscriptionCollectorClient instance
                super().__init__(*args, **kwargs)
            
            def do_GET(self):
                server_websocket_healthy = self.transcription_server_instance.is_healthy
                
                redis_healthy = False
                redis_ping_error = "Collector client not initialized"
                if self.redis_collector: # Check if collector_client was initialized
                    # Access redis_client via the stored reference
                    if self.redis_collector.redis_client: 
                        try:
                            with self.redis_collector.connection_lock:
                                if self.redis_collector.redis_client: # Double check under lock
                                    self.redis_collector.redis_client.ping()
                                    redis_healthy = True
                                    redis_ping_error = "None"
                                else:
                                    redis_ping_error = "redis_collector.redis_client is None (within lock)"
                        except redis.exceptions.RedisError as e:
                            redis_ping_error = str(e) # Typo fixed: redis_ping_Error -> redis_ping_error
                            logging.warning(f"Health check: Redis ping failed: {e}")
                        except Exception as e:
                            redis_ping_error = f"Unexpected error during ping: {str(e)}"
                            logging.warning(f"Health check: Unexpected error during Redis ping: {e}")
                    else: # redis_collector exists but its redis_client is None
                        redis_ping_error = "redis_collector.redis_client is None (implies not connected or error in worker)"
                
                if self.path == '/health':
                    if server_websocket_healthy and redis_healthy:
                        self.send_response(200)
                        self.send_header('Content-type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(b'OK')
                    else:
                        unhealthy_reasons = []
                        if not server_websocket_healthy:
                            unhealthy_reasons.append("WebSocket server not ready")
                        if not redis_healthy:
                            unhealthy_reasons.append(f"Redis connection unhealthy (ping error: {redis_ping_error})")
                        
                        logging.warning(f"Health check failed: {', '.join(unhealthy_reasons)}")
                        self.send_response(503)
                        self.send_header('Content-type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(f"Service Unavailable: {', '.join(unhealthy_reasons)}".encode('utf-8'))
                
                elif self.path == '/metrics':
                    # Provide JSON metrics for load monitoring
                    import json
                    import hashlib
                    
                    # Handle case where transcription_server_instance is None
                    if self.transcription_server_instance is None:
                        current_sessions = 0
                        max_clients = 10
                        server_id = 'unknown'
                        uid_list = []
                        token_hashes = []
                    else:
                        current_sessions = len(self.transcription_server_instance.client_manager.clients)
                        max_clients = getattr(self.transcription_server_instance, 'max_clients', 10)
                        server_id = getattr(self.transcription_server_instance, '_consul_service_id', 'unknown')
                        # Collect current client UIDs and token hashes for deduplication across servers
                        try:
                            uid_list = [
                                getattr(client, 'client_uid', None)
                                for client in self.transcription_server_instance.client_manager.clients.values()
                                if client is not None
                            ]
                            raw_tokens = [
                                getattr(client, 'token', None)
                                for client in self.transcription_server_instance.client_manager.clients.values()
                                if client is not None
                            ]
                            token_hashes = [
                                hashlib.sha1(t.encode('utf-8')).hexdigest()[:16]
                                for t in raw_tokens if isinstance(t, str) and len(t) > 0
                            ]
                        except Exception:
                            uid_list = []
                            token_hashes = []
                    
                    metrics = {
                        "current_sessions": current_sessions,
                        "max_clients": max_clients,
                        "load_percentage": (current_sessions / max_clients * 100) if max_clients > 0 else 0,
                        "server_healthy": server_websocket_healthy,
                        "redis_healthy": redis_healthy,
                        "server_id": server_id,
                        "active_uid_count": len([u for u in uid_list if u]),
                        "active_token_count": len(set(token_hashes)),
                        "active_token_hashes": token_hashes,
                        "timestamp": time.time()
                    }
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(metrics).encode('utf-8'))
                else:
                    self.send_response(404)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b'Not Found')
            
            # Silence server logs by default, can be enabled for debugging
            def log_message(self, format, *args):
                # logging.info(f"HealthCheck: {format % args}")
                return
        
        # Create a partial function to pass instance references to the handler
        handler_with_context = functools.partial(
            HealthCheckHandler,
            transcription_server_ref=parent_server_instance, # TranscriptionServer's self
            redis_collector_ref=parent_server_instance.collector_client # The collector instance from TranscriptionServer
        )
        
        try:
            self.health_server = socketserver.TCPServer((host, port), handler_with_context)
            
            # Start server in a new thread
            health_thread = threading.Thread(target=self.health_server.serve_forever)
            health_thread.daemon = True  # So it stops when the main thread stops
            health_thread.start()
            
            logging.info(f"Health check HTTP server started on {host}:{port}")
        except Exception as e:
            logging.error(f"Failed to start health check server: {e}")
            # If health server fails to start, it's a critical issue.
            # self.is_healthy might not be accurate for the self-monitor if this fails early.
            # Consider setting self.is_healthy = False here or exiting if http health server is mandatory.

    def handle_control_message(self, websocket, message):
        """Handles incoming control messages from the client."""
        client = self.client_manager.get_client(websocket) # CORRECTED
        if not client:
            logging.warning("Control message from unknown client (websocket not in client list).")
            # Optionally, close websocket if it's unrecognized and sending control messages
            # For now, just return to prevent further processing.
            try:
                # Example: Politely close or just ignore
                # await websocket.close(code=1008, reason="Unrecognized client")
                logging.info(f"Ignoring control message from unrecognized websocket: {websocket.remote_address}")
            except Exception as e:
                logging.error(f"Error handling unrecognized client during control message: {e}")
            return

        try:
            control_message = json.loads(message)
            message_type = control_message.get("type")
            
            if WL_LOG_CONTROL_EVENTS:
                logging.info(f"Received control message type: {message_type} from UID {client.uid if client else 'N/A'}")

            if message_type == "speaker_event": 
                # This path might be for older/different speaker events or specific debug.
                # The primary path for Phase 2+ speaker activity is "speaker_activity".
                # Assuming handle_speaker_event is a distinct, existing handler.
                self.handle_speaker_event(websocket, control_message) 
            elif message_type == "session_control":
                self.handle_session_control(websocket, control_message)
            elif message_type == "speaker_activity": # TARGET FOR PHASE 2
                logging.info(f"DISPATCH_DEBUG: Entered 'speaker_activity' branch for UID {client.uid if client else 'N/A'}. Calling handle_speaker_activity_update.") # <-- ADDED THIS LINE
                self.handle_speaker_activity_update(websocket, control_message)
            elif message_type == "audio_chunk_metadata":
                self.handle_audio_chunk_metadata(websocket, control_message)
            else:
                logging.warning(f"Unknown control message type: {message_type} from UID {client.uid if client else 'N/A'}")
        except json.JSONDecodeError:
            logging.error(f"Failed to decode JSON from control message from UID {client.uid if client else 'N/A'}: {message}")
        except Exception as e:
            logging.error(f"Error processing control message from UID {client.uid if client else 'N/A'}: {e}")


    def handle_speaker_activity_update(self, websocket, control_message):
        """
        Handles incoming 'speaker_activity' updates from the client (Vexa Bot).
        For Phase 2, this will forward the event payload to a new Redis stream.
        """
        client = self.client_manager.get_client(websocket) # CORRECTED
        # This check is good even if also done in handle_control_message, 
        # in case this method is ever called directly.
        if not client:
            logging.warning("handle_speaker_activity_update called but no client found for websocket.")
            return

        event_payload = control_message.get("payload")
        if not event_payload or not isinstance(event_payload, dict):
            logging.warning(f"Received speaker_activity with missing or invalid payload from UID {client.client_uid if client else 'N/A'}") # CORRECTED
            return

        # Use UID from payload if available, fallback to client.client_uid (they should match)
        uid_for_log = event_payload.get('uid', client.client_uid if client else 'N/A_CLIENT_FALLBACK')  # CORRECTED
        event_type = event_payload.get('event_type', 'N/A')
        participant_name = event_payload.get('participant_name', 'N/A')
        relative_ts = event_payload.get('relative_client_timestamp_ms', 'N/A')
        
        if WL_LOG_SPEAKER_EVENTS:
            logging.info(
                f"Processing Speaker Activity Update for UID {uid_for_log}: Type='{event_type}', Name='{participant_name}', RelativeTs={relative_ts}ms (Client on record: {client.client_uid if client else 'N/A_CLIENT_FALLBACK'})"
            )

        if client.collector_client:  # CORRECTED: changed from collector_client_ref to collector_client
            # The event_payload is what Vexa Bot sends.
            # The publish_speaker_event method in collector_client will add server_received_timestamp_iso.
            success = client.collector_client.publish_speaker_event(event_payload)  # CORRECTED: changed from collector_client_ref to collector_client
            if success:
                # Log already happens in publish_speaker_event, this is just confirmation of successful call
                logging.debug(f"Successfully queued speaker event for UID {uid_for_log} to Redis via collector_client.")
            else:
                logging.error(f"Failed to queue speaker event for UID {uid_for_log} to Redis via collector_client.")
        else:
            logging.warning(f"Cannot forward speaker event for UID {uid_for_log}: collector_client not found for client {client.client_uid if client else 'N/A_CLIENT_FALLBACK'}.") # CORRECTED: changed from collector_client_ref to collector_client

        # Update server-level last speaker-event timestamp
        try:
            self.last_speaker_event_ts = time.time()
        except Exception:
            pass


    def handle_audio_chunk_metadata(self, websocket, control_message):
        client = self.client_manager.get_client(websocket)
        if not client:
            logging.warning("No client found for audio chunk metadata handling.")
            return

        try:
            payload = control_message.get("payload", {})
            logging.debug(f"Audio Chunk Metadata received: {payload}")
            
            # Future Phase 2: Could be used for audio quality monitoring, chunk timing analysis, etc.
            # For now, just log at debug level to avoid cluttering logs
            
        except Exception as e:
            logging.error(f"Error processing audio chunk metadata: {e}")


class ServeClientBase(object):
    RATE = 16000
    SERVER_READY = "SERVER_READY"
    DISCONNECT = "DISCONNECT"
    
    # Hallucination filter - load once per class
    _hallucinations = None
    _hallucinations_loaded = False

    def __init__(self, websocket, language="en", task="transcribe", client_uid=None, 
                 platform=None, meeting_url=None, token=None, meeting_id=None,
                 transcription_tier: str = "realtime",
                 collector_client_ref: Optional[TranscriptionCollectorClient] = None,
                 server_options: Optional[dict] = None):
        self.websocket = websocket
        # Track whether language was explicitly provided (not None)
        # This helps optimize language detection when language is not provided
        self.language_provided = language is not None
        self.language = language
        self.task = task
        self.client_uid = client_uid or str(uuid.uuid4())
        self.platform = platform
        self.meeting_url = meeting_url
        self.token = token
        self.meeting_id = meeting_id
        normalized_tier = str(transcription_tier or "realtime").strip().lower()
        self.transcription_tier = normalized_tier if normalized_tier in ("realtime", "deferred") else "realtime"
        self.collector_client = collector_client_ref # Store the passed collector client
        
        # Restore all the original instance variables that were deleted
        self.transcription_buffer = TranscriptionBuffer(self.client_uid)
        self.model = None
        self.is_multilingual = True
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

        server_options = server_options or {}
        self.max_buffer_s = server_options.get("max_buffer_s", 45)
        self.discard_buffer_s = server_options.get("discard_buffer_s", 30)
        self.clip_if_no_segment_s = server_options.get("clip_if_no_segment_s", 25)
        self.clip_retain_s = server_options.get("clip_retain_s", 5)

        self.show_prev_out_thresh = server_options.get("show_prev_out_thresh_s", 5)   # if pause(no output from whisper) show previous output for 5 seconds
        self.add_pause_thresh = server_options.get("add_pause_thresh_s", 3)       # add a blank to segment list as a pause(no speech) for 3 seconds
        self.transcript = []
        self.send_last_n_segments = 10

        # text formatting
        self.pick_previous_segments = 2

        # threading
        self.lock = threading.Lock()
        self._recording_lock = threading.Lock()

        # Durable recording spool (issue #112): write incoming float32 frames
        # to persistent chunk files while keeping in-memory realtime flow intact.
        self.wl_recording_dir = str(server_options.get("wl_recording_dir", "/tmp/wl-recordings")).strip()
        self.wl_recording_flush_seconds = max(0.0, float(server_options.get("wl_recording_flush_seconds", 3.0)))
        self.wl_recording_fsync_seconds = max(0.0, float(server_options.get("wl_recording_fsync_seconds", 10.0)))
        self.wl_recording_rotate_seconds = max(1.0, float(server_options.get("wl_recording_rotate_seconds", 20.0)))
        self.wl_recording_rotate_bytes = max(1024, int(server_options.get("wl_recording_rotate_bytes", 16 * 1024 * 1024)))
        self.wl_recording_snapshot_seconds = max(0.0, float(server_options.get("wl_recording_snapshot_seconds", 20.0)))
        self._recording_chunk_dir: Optional[Path] = None
        self._recording_chunk_handle = None
        self._recording_chunk_start_monotonic = 0.0
        self._recording_chunk_size_bytes = 0
        self._recording_last_flush_monotonic = 0.0
        self._recording_last_fsync_monotonic = 0.0
        self._recording_chunk_index = 0
        self._recording_manifest: List[dict] = []
        self._recording_finalized = False
        self._recording_upload_in_flight = False
        self._recording_upload_lock = threading.Lock()
        self._recording_last_snapshot_upload_monotonic = 0.0
        self._init_recording_spool()
        
        # Send SERVER_READY message
        ready_message = json.dumps({"status": self.SERVER_READY, "uid": self.client_uid})
        logging.info(f"Client {self.client_uid} connected. Sending SERVER_READY.")
        self.websocket.send(ready_message)
        
        # Use the instance's self.collector_client
        if self.collector_client and all([platform, meeting_url, token, meeting_id]):
            self.collector_client.publish_session_start_event(token, platform, meeting_id, self.client_uid)
            logging.info(f"Published session_start event for client {self.client_uid}")
        
        # Load hallucination filter
        self._load_hallucinations()

    def speech_to_text(self):
        raise NotImplementedError
    
    def _load_hallucinations(self):
        """Load hallucination strings from file if not already loaded."""
        if ServeClientBase._hallucinations_loaded:
            return
            
        try:
            # Collect hallucination strings from multiple sources:
            # - Single files: /app/hallucinations.txt and local hallucinations.txt
            # - Language folders: /app/hallucinations/** and local ../hallucinations/**
            script_dir = os.path.dirname(os.path.abspath(__file__))
            candidates = []

            # Single-file locations (backward compatible)
            app_root_file = "/app/hallucinations.txt"
            local_root_file = os.path.join(script_dir, "..", "hallucinations.txt")
            if os.path.exists(app_root_file):
                candidates.append(app_root_file)
            if os.path.exists(local_root_file):
                candidates.append(local_root_file)

            # Folder-based locations (language-separated files)
            app_dir = "/app/hallucinations"
            local_dir = os.path.join(script_dir, "..", "hallucinations")
            for directory in (app_dir, local_dir):
                if os.path.isdir(directory):
                    for root, _dirs, files in os.walk(directory):
                        for name in files:
                            # Accept common text list extensions
                            if name.lower().endswith((".txt", ".list")):
                                candidates.append(os.path.join(root, name))

            # Read and deduplicate entries across all sources
            unique_entries = set()
            loaded_files = 0
            for path in candidates:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        for line in f:
                            normalized = line.strip().lower()
                            if normalized:
                                unique_entries.add(normalized)
                    loaded_files += 1
                    logging.info(f"Loaded hallucination filters from {path}")
                except Exception as read_err:
                    logging.warning(f"Failed to read hallucination file {path}: {read_err}")

            ServeClientBase._hallucinations = sorted(unique_entries)
            logging.info(
                f"Loaded {len(ServeClientBase._hallucinations)} unique hallucination filters from {loaded_files} file(s)"
            )
        except Exception as e:
            logging.error(f"Error loading hallucination filters: {e}")
            ServeClientBase._hallucinations = []
        
        ServeClientBase._hallucinations_loaded = True
    
    def _filter_hallucinations(self, text):
        """Filter out hallucination strings from transcription text."""
        if not ServeClientBase._hallucinations or not text:
            return text
            
        # Convert to lowercase for comparison
        text_lower = text.lower().strip()
        
        # Check if the entire text matches any hallucination
        for hallucination in ServeClientBase._hallucinations:
            if text_lower == hallucination:
                logging.debug(f"Filtered hallucination: '{text}' matches '{hallucination}'")
                return None  # Return None to indicate this should be omitted
        
        return text  # Return original text if no hallucination detected

    def transcribe_audio(self):
        raise NotImplementedError

    def handle_transcription_output(self):
        raise NotImplementedError

    def _init_recording_spool(self):
        if not self.wl_recording_dir:
            return
        try:
            self._recording_chunk_dir = Path(self.wl_recording_dir) / self.client_uid
            self._recording_chunk_dir.mkdir(parents=True, exist_ok=True)
            self._open_new_recording_chunk()
            logging.info(f"WL durable recording enabled for {self.client_uid}: {self._recording_chunk_dir}")
        except Exception as e:
            logging.error(f"Failed to initialize WL recording spool for {self.client_uid}: {e}")
            self._recording_chunk_dir = None
            self._recording_chunk_handle = None

    def _open_new_recording_chunk(self):
        if self._recording_chunk_dir is None:
            return
        now = int(time.time())
        chunk_name = f"{now}_{self._recording_chunk_index:06d}.f32"
        chunk_path = self._recording_chunk_dir / chunk_name
        self._recording_chunk_handle = open(chunk_path, "ab")
        self._recording_chunk_start_monotonic = time.monotonic()
        self._recording_last_flush_monotonic = self._recording_chunk_start_monotonic
        self._recording_last_fsync_monotonic = self._recording_chunk_start_monotonic
        self._recording_chunk_size_bytes = 0
        self._recording_manifest.append({"chunk": chunk_name, "created_at": datetime.datetime.utcnow().isoformat()})
        self._recording_chunk_index += 1

    def _rotate_recording_chunk_if_needed(self, now_mono: float):
        if self._recording_chunk_handle is None:
            return
        age_s = now_mono - self._recording_chunk_start_monotonic
        if self._recording_chunk_size_bytes >= self.wl_recording_rotate_bytes or age_s >= self.wl_recording_rotate_seconds:
            try:
                self._recording_chunk_handle.flush()
                os.fsync(self._recording_chunk_handle.fileno())
                self._recording_chunk_handle.close()
            except Exception:
                pass
            self._open_new_recording_chunk()

    def _append_to_recording_spool(self, frame_np: np.ndarray):
        if self._recording_chunk_handle is None:
            return
        try:
            payload = frame_np.astype(np.float32, copy=False).tobytes()
            now_mono = time.monotonic()
            with self._recording_lock:
                self._rotate_recording_chunk_if_needed(now_mono)
                if self._recording_chunk_handle is None:
                    return
                self._recording_chunk_handle.write(payload)
                self._recording_chunk_size_bytes += len(payload)
                if self.wl_recording_flush_seconds == 0 or (now_mono - self._recording_last_flush_monotonic) >= self.wl_recording_flush_seconds:
                    self._recording_chunk_handle.flush()
                    self._recording_last_flush_monotonic = now_mono
                if self.wl_recording_fsync_seconds == 0 or (now_mono - self._recording_last_fsync_monotonic) >= self.wl_recording_fsync_seconds:
                    os.fsync(self._recording_chunk_handle.fileno())
                    self._recording_last_fsync_monotonic = now_mono
        except Exception as e:
            logging.error(f"Failed to append recording chunk for {self.client_uid}: {e}")

    def _finalize_recording_spool(self):
        if self._recording_finalized:
            return
        self._recording_finalized = True
        if self._recording_chunk_dir is None:
            return
        try:
            with self._recording_lock:
                if self._recording_chunk_handle is not None:
                    try:
                        self._recording_chunk_handle.flush()
                        os.fsync(self._recording_chunk_handle.fileno())
                    finally:
                        self._recording_chunk_handle.close()
                        self._recording_chunk_handle = None

            manifest_path = self._recording_chunk_dir / "manifest.json"
            manifest = {
                "session_uid": self.client_uid,
                "rate": self.RATE,
                "dtype": "float32",
                "channels": 1,
                "chunk_format": "f32le",
                "chunks": self._recording_manifest,
                "transcription_tier": self.transcription_tier,
                "finalized_at": datetime.datetime.utcnow().isoformat(),
            }
            manifest_path.write_text(json.dumps(manifest, separators=(",", ":"), ensure_ascii=True))
            logging.info(f"Finalized WL recording spool for {self.client_uid}: {manifest_path}")
        except Exception as e:
            logging.error(f"Failed finalizing WL recording spool for {self.client_uid}: {e}")

    def _render_spool_to_wav(self) -> Optional[tuple[str, float]]:
        """
        Convert persisted float32le spool chunks to a temporary WAV file.
        Returns tuple (wav_path, duration_seconds) on success.
        """
        if self._recording_chunk_dir is None or not self._recording_manifest:
            return None
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="wb",
                suffix=".wav",
                prefix=f"wl-{self.client_uid}-",
                delete=False,
            )
            wav_path = tmp.name
            tmp.close()

            import wave

            # Ensure currently-open chunk is visible to reader.
            with self._recording_lock:
                if self._recording_chunk_handle is not None:
                    self._recording_chunk_handle.flush()

            total_samples = 0
            with wave.open(wav_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # int16
                wav_file.setframerate(self.RATE)

                for chunk_meta in self._recording_manifest:
                    chunk_name = chunk_meta.get("chunk")
                    if not chunk_name:
                        continue
                    chunk_path = self._recording_chunk_dir / chunk_name
                    if not chunk_path.exists():
                        logging.warning(f"WL spool chunk missing for {self.client_uid}: {chunk_path}")
                        continue

                    raw = chunk_path.read_bytes()
                    if not raw:
                        continue
                    frames_f32 = np.frombuffer(raw, dtype=np.float32)
                    if frames_f32.size == 0:
                        continue
                    frames_i16 = np.clip(frames_f32, -1.0, 1.0)
                    frames_i16 = (frames_i16 * 32767.0).astype(np.int16)
                    wav_file.writeframes(frames_i16.tobytes())
                    total_samples += int(frames_i16.size)

            duration_seconds = (total_samples / float(self.RATE)) if total_samples > 0 else 0.0
            if duration_seconds <= 0:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
                return None
            return wav_path, duration_seconds
        except Exception as e:
            logging.error(f"Failed to render WL spool to WAV for {self.client_uid}: {e}", exc_info=True)
            return None

    def _upload_recording_spool_to_bot_manager(self, is_final: bool):
        """
        Best-effort handoff: upload finalized recording to bot-manager internal endpoint.
        """
        upload_enabled = str(os.getenv("WL_RECORDING_UPLOAD_ENABLED", "true")).strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        if not upload_enabled:
            return

        upload_url = os.getenv(
            "WL_RECORDING_UPLOAD_URL",
            os.getenv("BOT_MANAGER_RECORDING_UPLOAD_URL", "http://bot-manager:8080/internal/recordings/upload"),
        ).strip()
        if not upload_url:
            logging.warning(f"WL recording upload skipped for {self.client_uid}: upload URL is empty")
            return

        if not self.client_uid:
            logging.warning("WL recording upload skipped: missing session UID")
            return

        rendered = self._render_spool_to_wav()
        if not rendered:
            logging.info(f"WL recording upload skipped for {self.client_uid}: no audio frames")
            return

        wav_path, duration_seconds = rendered
        timeout_seconds = max(5.0, float(os.getenv("WL_RECORDING_UPLOAD_TIMEOUT_SECONDS", "180")))

        try:
            with open(wav_path, "rb") as wav_file:
                files = {"file": (f"{self.client_uid}.wav", wav_file, "audio/wav")}
                data = {
                    "session_uid": self.client_uid,
                    "media_type": "audio",
                    "media_format": "wav",
                    "sample_rate": str(self.RATE),
                    "duration_seconds": str(round(duration_seconds, 3)),
                    "is_final": "true" if is_final else "false",
                }
                with httpx.Client(timeout=timeout_seconds) as client:
                    response = client.post(upload_url, files=files, data=data)
                if response.status_code >= 400:
                    logging.error(
                        f"WL recording upload failed for {self.client_uid}: "
                        f"status={response.status_code}, body={response.text[:500]}"
                    )
                else:
                    logging.info(
                        f"WL recording upload succeeded for {self.client_uid}: "
                        f"status={response.status_code}, duration={duration_seconds:.2f}s, final={is_final}"
                    )
        except Exception as e:
            logging.error(f"WL recording upload exception for {self.client_uid}: {e}", exc_info=True)
        finally:
            try:
                os.remove(wav_path)
            except Exception:
                pass

    def _start_snapshot_upload_if_due(self):
        """
        Upload periodic recording snapshots during the meeting.
        This keeps object storage up to date while preserving existing in-memory realtime path.
        """
        if self.wl_recording_snapshot_seconds <= 0:
            return
        if self._recording_finalized:
            return
        now_mono = time.monotonic()
        if (now_mono - self._recording_last_snapshot_upload_monotonic) < self.wl_recording_snapshot_seconds:
            return
        with self._recording_upload_lock:
            if self._recording_upload_in_flight:
                return
            self._recording_upload_in_flight = True
            self._recording_last_snapshot_upload_monotonic = now_mono

        def _runner():
            try:
                self._upload_recording_spool_to_bot_manager(is_final=False)
            finally:
                with self._recording_upload_lock:
                    self._recording_upload_in_flight = False

        threading.Thread(target=_runner, daemon=True).start()

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
        self._append_to_recording_spool(frame_np)
        self.lock.acquire()
        if self.frames_np is not None and self.frames_np.shape[0] > self.max_buffer_s * self.RATE:
            self.frames_offset += self.discard_buffer_s
            self.frames_np = self.frames_np[int(self.discard_buffer_s * self.RATE):]
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
        self._start_snapshot_upload_if_due()

    def clip_audio_if_no_valid_segment(self):
        """
        Update the timestamp offset based on audio buffer status.
        Clip audio if the current chunk exceeds 30 seconds, this basically implies that
        no valid segment for the last 30 seconds from whisper
        """
        with self.lock:
            if self.frames_np[int((self.timestamp_offset - self.frames_offset)*self.RATE):].shape[0] > self.clip_if_no_segment_s * self.RATE:
                duration = self.frames_np.shape[0] / self.RATE
                self.timestamp_offset = self.frames_offset + duration - self.clip_retain_s

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

        This method formats the transcription segments into a JSON object and attempts to send
        this object to the client. If an error occurs during the send operation, it logs the error.

        Returns:
            segments (list): A list of transcription segments to be sent to the client.
        """
        try:
            # Validate required client properties
            if not self.platform or not self.meeting_url or not self.token:
                logging.error(f"ERROR: Missing required fields for client {self.client_uid}: platform={self.platform}, meeting_url={self.meeting_url}, token={self.token}")
                # Don't default to unknown anymore, force these to be set properly
                return
                
            data = {
                "uid": self.client_uid,
                "segments": segments,
            }
            self.websocket.send(json.dumps(data))
            
            # Use the instance's self.collector_client
            if self.collector_client:
                self.collector_client.send_transcription(
                    token=self.token,
                    platform=self.platform,
                    meeting_id=self.meeting_id,
                    segments=segments,
                    session_uid=self.client_uid
                )
            
            # Logging: summary by default; full text only if WL_LOG_TRANSCRIPTS=true
            try:
                total = len(segments)
                completed = sum(1 for s in segments if s.get('completed'))
                last = segments[-1] if total else {}
                last_range = f"{last.get('start','N/A')}-{last.get('end','N/A')}" if last else "N/A"
                last_completed = bool(last.get('completed')) if last else None
                lang = last.get('language') if last else None
                if WL_LOG_TRANSCRIPTS:
                    formatted_segments = []
                    for i, segment in enumerate(segments):
                        if 'start' in segment and 'end' in segment:
                            formatted_segments.append(
                                f"[{i}] ({segment.get('start', 'N/A')}-{segment.get('end', 'N/A')}) "
                                f"[{'COMPLETE' if segment.get('completed', False) else 'PARTIAL'}]: "
                                f"\"{segment.get('text', '')}\""
                            )
                        else:
                            formatted_segments.append(f"[{i}]: \"{segment.get('text', '')}\"")
                    logger.info(
                        f"TRANSCRIPTION_FULL: client={self.client_uid}, platform={self.platform}, meeting_id={self.meeting_id}, count={total}\n" +
                        "\n".join(formatted_segments)
                    )
                elif WL_LOG_TRANSCRIPT_SUMMARY:
                    logger.info(
                        f"TX_SUMMARY: client={self.client_uid}, platform={self.platform}, meeting_id={self.meeting_id}, count={total}, completed={completed}, last={last_range}, last_completed={last_completed}, lang={lang}"
                    )
            except Exception:
                pass
            # Update server-level last transcription timestamp for circuit breaker
            try:
                from time import time as _now
                if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                    self.collector_client.server_ref.server_last_transcription_ts = _now()
                else:
                    globals().setdefault('_WL_SERVER_LAST_TX', 0)
                    globals()['_WL_SERVER_LAST_TX'] = _now()
            except Exception:
                pass
        except Exception as e:
            logging.error(f"[ERROR]: Sending data to client: {e}")

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
        self._finalize_recording_spool()
        self._upload_recording_spool_to_bot_manager(is_final=True)
        self.exit = True

    def forward_to_collector(self, segments):
        """Forward transcriptions to the collector if available"""
        if self.collector_client and segments:
            # Send transcription to collector
            self.collector_client.send_transcription(
                token=self.token,
                platform=self.platform,
                meeting_id=self.meeting_id,
                segments=segments,
                session_uid=self.client_uid
            )


class ServeClientTensorRT(ServeClientBase):

    SINGLE_MODEL = None
    SINGLE_MODEL_LOCK = threading.Lock()

    def __init__(self, websocket, task="transcribe", multilingual=False, language=None, 
                 client_uid=None, model=None, single_model=False, 
                 platform=None, meeting_url=None, token=None, meeting_id=None,
                 transcription_tier: str = "realtime",
                 collector_client_ref: Optional[TranscriptionCollectorClient] = None,
                 server_options: Optional[dict] = None):
        super().__init__(websocket, language, task, client_uid, platform, meeting_url, token, meeting_id,
                         transcription_tier=transcription_tier,
                         collector_client_ref=collector_client_ref, server_options=server_options)
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
        if not TENSORRT_AVAILABLE:
            raise RuntimeError(
                "TensorRT dependencies are not available. Please install TensorRT libraries or use the faster_whisper backend instead."
            )
            
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
        logging.debug(f"[WhisperTensorRT:] Processing audio with duration: {input_bytes.shape[0] / self.RATE}")
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
            
            segment_data = {
                "text": last_segment + " ", 
                "start": "{:.3f}".format(start_time),
                "end": "{:.3f}".format(end_time),
                "completed": True
            }
            
            # Add language if available
            if self.language is not None:
                segment_data["language"] = self.language
            
            if not len(self.transcript):
                self.transcript.append(segment_data)
            elif self.transcript[-1]["text"].strip() != last_segment:
                self.transcript.append(segment_data)
            
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
                logging.debug(f"[WhisperTensorRT:] Processing audio with duration: {duration}")
                self.transcribe_audio(input_sample)

            except Exception as e:
                logging.error(f"[ERROR]: {e}")

    def format_segment(self, start, end, text, completed=False, language=None):
        """
        Formats a transcription segment with precise start and end times alongside the transcribed text.

        Args:
            start (float): The start time of the transcription segment in seconds.
            end (float): The end time of the transcription segment in seconds.
            text (str): The transcribed text corresponding to the segment.
            completed (bool): Whether the segment is completed or partial.
            language (str): The detected language for this segment.

        Returns:
            dict: A dictionary representing the formatted transcription segment, including
                'start' and 'end' times as strings with three decimal places, the 'text'
                of the transcription, 'completed' status, and 'language' if provided.
        """
        segment = {
            'start': "{:.3f}".format(start),
            'end': "{:.3f}".format(end),
            'text': text,
            'completed': completed
        }
        
        # Add language if provided
        if language is not None:
            segment['language'] = language
            
        return segment

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
                # Update circuit-breaker timestamp BEFORE filtering, so hallucinations still count as activity
                try:
                    if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                        self.collector_client.server_ref.server_last_transcription_ts = time.time()
                except Exception:
                    pass

                # Apply hallucination filter
                filtered_text = self._filter_hallucinations(text_)
                if filtered_text is None:
                    # Log and skip this segment if it's a hallucination
                    try:
                        if WL_LOG_HALLUCINATIONS:
                            logger.info(f'HALLUCINATION_FILTERED: "{text_}"')
                    except Exception:
                        pass
                    continue
                
                self.text.append(filtered_text)
                with self.lock:
                    start, end = self.timestamp_offset + s.start, self.timestamp_offset + min(duration, s.end)

                if start >= end:
                    continue
                if s.no_speech_prob > self.no_speech_thresh:
                    continue

                self.transcript.append(self.format_segment(start, end, filtered_text, completed=True, language=self.language))
                offset = min(duration, s.end)

        # only process the last segment if it satisfies the no_speech_thresh
        if segments[-1].no_speech_prob <= self.no_speech_thresh:
            # Update circuit-breaker timestamp BEFORE filtering for the last (partial) segment
            try:
                if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                    self.collector_client.server_ref.server_last_transcription_ts = time.time()
            except Exception:
                pass

            # Apply hallucination filter to the current output
            filtered_current_out = self._filter_hallucinations(segments[-1].text)
            if filtered_current_out is not None:
                self.current_out += filtered_current_out
                with self.lock:
                    last_segment = self.format_segment(
                        self.timestamp_offset + segments[-1].start,
                        self.timestamp_offset + min(duration, segments[-1].end),
                        self.current_out,
                        completed=False,
                        language=self.language
                    )
            else:
                # Log and skip this segment if it's a hallucination
                try:
                    if WL_LOG_HALLUCINATIONS:
                        logger.info(f'HALLUCINATION_FILTERED: "{segments[-1].text}"')
                except Exception:
                    pass
                last_segment = None

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
                # Update circuit-breaker timestamp BEFORE filtering repeated incomplete output
                try:
                    if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                        self.collector_client.server_ref.server_last_transcription_ts = time.time()
                except Exception:
                    pass

                # Apply hallucination filter before adding to transcript
                filtered_current_out = self._filter_hallucinations(self.current_out)
                if filtered_current_out is not None:
                    self.text.append(filtered_current_out)
                    with self.lock:
                        self.transcript.append(self.format_segment(
                            self.timestamp_offset,
                            self.timestamp_offset + min(duration, self.end_time_for_same_output),
                            filtered_current_out,
                            completed=True,
                            language=self.language
                        ))
                else:
                    # Log filtered repeated hallucination
                    try:
                        if WL_LOG_HALLUCINATIONS:
                            logger.info(f'HALLUCINATION_FILTERED: "{self.current_out}"')
                    except Exception:
                        pass
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

    def set_language(self, info):
        """
        Updates the language attribute based on the detected language information.

        Args:
            info (object): An object containing the detected language and its probability. This object
                        must have at least two attributes: `language`, a string indicating the detected
                        language, and `language_probability`, a float representing the confidence level
                        of the language detection.
        """
        if hasattr(info, 'language_probability') and info.language_probability > 0.5:
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

class ServeClientFasterWhisper(ServeClientBase):

    SINGLE_MODEL = None
    SINGLE_MODEL_LOCK = threading.Lock()

    def __init__(self, websocket, task="transcribe", device=None, language=None, 
                 client_uid=None, model="small.en", initial_prompt=None, 
                 vad_parameters=None, use_vad=True, single_model=False, 
                 platform=None, meeting_url=None, token=None, meeting_id=None,
                 transcription_tier: str = "realtime",
                 collector_client_ref: Optional[TranscriptionCollectorClient] = None,
                 server_options: Optional[dict] = None):
        super().__init__(websocket, language, task, client_uid, platform, meeting_url, token, meeting_id,
                         transcription_tier=transcription_tier,
                         collector_client_ref=collector_client_ref, server_options=server_options)
        self.model_sizes = [
            "tiny", "tiny.en", "base", "base.en", "small", "small.en",
            "medium", "medium.en", "large-v2", "large-v3", "distil-small.en",
            "distil-medium.en", "distil-large-v2", "distil-large-v3",
            "large-v3-turbo", "turbo"
        ]
        
        # Log the critical parameters
        logging.info(f"Initializing FasterWhisper client {client_uid} with platform={platform}, meeting_url={meeting_url}, token={token}")

        self.model_size_or_path = model
        # If model is English-only, auto-set language to "en" (this counts as provided)
        if self.model_size_or_path.endswith("en"):
            self.language = "en"
            self.language_provided = True  # Model-based language is considered "provided"
        else:
            self.language = language
            # language_provided is already set in base class based on original language parameter
        self.task = task
        self.initial_prompt = initial_prompt

        server_options = server_options or {}
        self.min_audio_s = server_options.get("min_audio_s", 1.0)
        logging.info(f"FasterWhisper client {client_uid}: min_audio_s={self.min_audio_s} (server_options had: {server_options.get('min_audio_s', 'NOT SET')})")
        self.vad_parameters = vad_parameters or {"onset": server_options.get("vad_onset", 0.5)}
        self.no_speech_thresh = server_options.get("vad_no_speech_thresh", 0.45)
        self.same_output_threshold = server_options.get("same_output_threshold", 10)
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
        # Reduce language detection segments if language was not provided to speed up first transcription
        # Default is 10 segments (300 seconds), reduce to 1-2 segments (30-60 seconds) when auto-detecting
        language_detection_segments = 1 if not self.language_provided else int(os.getenv('LANGUAGE_DETECTION_SEGMENTS', '10'))
        result, info = self.transcriber.transcribe(
            input_sample,
            initial_prompt=self.initial_prompt,
            language=self.language,
            task=self.task,
            vad_filter=self.use_vad,
            vad_parameters=self.vad_parameters if self.use_vad else None,
            language_detection_segments=language_detection_segments)
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
            # Log when segments are updated (especially partial segments that get reevaluated)
            if last_segment and not last_segment.get('completed', True):
                logging.info(f"SEGMENT_UPDATE: client={self.client_uid}, partial_segment={last_segment.get('text', '')[:50]}, start={last_segment.get('start')}, end={last_segment.get('end')}")
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
            if duration < self.min_audio_s:
                time.sleep(0.1)     # wait for audio chunks to arrive
                continue
            try:
                input_sample = input_bytes.copy()
                result = self.transcribe_audio(input_sample)

                # Only block on language detection if language was not provided initially
                # If language was provided, we can send transcription immediately
                if result is None or (not self.language_provided and self.language is None):
                    self.timestamp_offset += duration
                    time.sleep(0.25)    # wait for voice activity, result is None when no voice activity
                    continue
                self.handle_transcription_output(result, duration)

            except Exception as e:
                logging.error(f"[ERROR]: Failed to transcribe audio chunk: {e}")
                time.sleep(0.01)

    def format_segment(self, start, end, text, completed=False, language=None):
        """
        Formats a transcription segment with precise start and end times alongside the transcribed text.

        Args:
            start (float): The start time of the transcription segment in seconds.
            end (float): The end time of the transcription segment in seconds.
            text (str): The transcribed text corresponding to the segment.
            completed (bool): Whether the segment is completed or partial.
            language (str): The detected language for this segment.

        Returns:
            dict: A dictionary representing the formatted transcription segment, including
                'start' and 'end' times as strings with three decimal places, the 'text'
                of the transcription, 'completed' status, and 'language' if provided.
        """
        segment = {
            'start': "{:.3f}".format(start),
            'end': "{:.3f}".format(end),
            'text': text,
            'completed': completed
        }
        
        # Add language if provided
        if language is not None:
            segment['language'] = language
            
        return segment

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

                # Update circuit-breaker timestamp BEFORE filtering, so hallucinations still count as activity
                try:
                    if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                        self.collector_client.server_ref.server_last_transcription_ts = time.time()
                except Exception:
                    pass

                # Apply hallucination filter
                filtered_text = self._filter_hallucinations(text_)
                if filtered_text is None:
                    # Log and skip this segment if it's a hallucination
                    try:
                        if WL_LOG_HALLUCINATIONS:
                            logger.info(f'HALLUCINATION_FILTERED: "{text_}"')
                    except Exception:
                        pass
                    continue
                
                self.text.append(filtered_text)
                with self.lock:
                    start, end = self.timestamp_offset + s.start, self.timestamp_offset + min(duration, s.end)

                if start >= end:
                    continue
                if s.no_speech_prob > self.no_speech_thresh:
                    continue

                self.transcript.append(self.format_segment(start, end, filtered_text, completed=True, language=self.language))
                offset = min(duration, s.end)

        # only process the last segment if it satisfies the no_speech_thresh
        if segments[-1].no_speech_prob <= self.no_speech_thresh:
            # Update circuit-breaker timestamp BEFORE filtering for the last (partial) segment
            try:
                if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                    self.collector_client.server_ref.server_last_transcription_ts = time.time()
            except Exception:
                pass

            # Apply hallucination filter to the current output
            filtered_current_out = self._filter_hallucinations(segments[-1].text)
            if filtered_current_out is not None:
                self.current_out += filtered_current_out
                with self.lock:
                    last_segment = self.format_segment(
                        self.timestamp_offset + segments[-1].start,
                        self.timestamp_offset + min(duration, segments[-1].end),
                        self.current_out,
                        completed=False,
                        language=self.language
                    )
            else:
                # Log and skip this segment if it's a hallucination
                try:
                    if WL_LOG_HALLUCINATIONS:
                        logger.info(f'HALLUCINATION_FILTERED: "{segments[-1].text}"')
                except Exception:
                    pass
                last_segment = None

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
                # Update circuit-breaker timestamp BEFORE filtering repeated incomplete output
                try:
                    if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                        self.collector_client.server_ref.server_last_transcription_ts = time.time()
                except Exception:
                    pass

                # Apply hallucination filter before adding to transcript
                filtered_current_out = self._filter_hallucinations(self.current_out)
                if filtered_current_out is not None:
                    self.text.append(filtered_current_out)
                    with self.lock:
                        self.transcript.append(self.format_segment(
                            self.timestamp_offset,
                            self.timestamp_offset + min(duration, self.end_time_for_same_output),
                            filtered_current_out,
                            completed=True,
                            language=self.language
                        ))
                else:
                    # Log filtered repeated hallucination
                    try:
                        if WL_LOG_HALLUCINATIONS:
                            logger.info(f'HALLUCINATION_FILTERED: "{self.current_out}"')
                    except Exception:
                        pass
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


class ServeClientRemote(ServeClientBase):

    def __init__(self, websocket, task="transcribe", language=None, 
                 client_uid=None, model=None, initial_prompt=None, 
                 vad_parameters=None, use_vad=True, 
                 platform=None, meeting_url=None, token=None, meeting_id=None,
                 transcription_tier: str = "realtime",
                 collector_client_ref: Optional[TranscriptionCollectorClient] = None,
                 server_options: Optional[dict] = None):
        super().__init__(websocket, language, task, client_uid, platform, meeting_url, token, meeting_id,
                         transcription_tier=transcription_tier,
                         collector_client_ref=collector_client_ref, server_options=server_options)
        
        # Log the critical parameters
        logging.info(f"Initializing Remote client {client_uid} with platform={platform}, meeting_url={meeting_url}, token={token}")

        # Ensure model is set, fallback to env var
        self.model = model or os.getenv("REMOTE_TRANSCRIBER_MODEL")
        self.language = language
        self.task = task
        self.initial_prompt = initial_prompt

        server_options = server_options or {}
        is_deferred_tier = self.transcription_tier == "deferred"
        self.min_audio_s = server_options.get("min_audio_s_tier2", 20.0) if is_deferred_tier else server_options.get("min_audio_s", 1.0)
        self.same_output_threshold = server_options.get("same_output_threshold_tier2", 2) if is_deferred_tier else server_options.get("same_output_threshold", 3)
        
        # Rate limiting: minimum time between requests per connection
        from whisper_live import settings
        # Use getattr with default in case settings doesn't have this attribute (backward compatibility)
        # Support both old MAX_TRANSCRIPTION_FREQUENCY_HZ (Hz) and new MIN_TIME_BETWEEN_REQUESTS_S (seconds)
        if hasattr(settings, 'MIN_TIME_BETWEEN_REQUESTS_S'):
            default_min_time = getattr(settings, 'MIN_TIME_BETWEEN_REQUESTS_S', 1.0)
        elif hasattr(settings, 'MAX_TRANSCRIPTION_FREQUENCY_HZ'):
            # Backward compatibility: convert Hz to seconds
            default_frequency_hz = getattr(settings, 'MAX_TRANSCRIPTION_FREQUENCY_HZ', 2.0)
            default_min_time = 1.0 / default_frequency_hz if default_frequency_hz > 0 else 0.0
        else:
            default_min_time = 1.0
        
        # Check server_options for either old or new parameter name
        min_time = server_options.get("min_time_between_requests_s", 
                                     server_options.get("max_transcription_frequency_hz", None))
        if min_time is None:
            self.min_time_between_requests = default_min_time
        else:
            # If value is <= 1.0, it might be Hz (old format) - convert to seconds
            # If value is > 1.0, it's already in seconds (new format)
            if min_time > 0 and min_time <= 1.0:
                # Old format: Hz -> convert to seconds
                self.min_time_between_requests = 1.0 / min_time
            else:
                # New format: already in seconds, use directly
                self.min_time_between_requests = min_time
        if is_deferred_tier:
            tier2_min_time = server_options.get("min_time_between_requests_s_tier2", 20.0)
            self.min_time_between_requests = float(tier2_min_time)
        
        self.last_transcription_time = 0.0  # Track when last transcription request completed
        
        logging.info(f"Remote client {client_uid}: transcription_tier={self.transcription_tier}")
        logging.info(f"Remote client {client_uid}: min_audio_s={self.min_audio_s}")
        logging.info(f"Remote client {client_uid}: same_output_threshold={self.same_output_threshold}")
        logging.info(f"Remote client {client_uid}: min_time_between_requests={self.min_time_between_requests:.3f}s (max {1.0/self.min_time_between_requests:.2f} requests/second)")
        
        self.vad_parameters = vad_parameters or {"onset": server_options.get("vad_onset", 0.5)}
        self.no_speech_thresh = server_options.get("vad_no_speech_thresh", 0.45)
        self.end_time_for_same_output = None

        if not REMOTE_AVAILABLE:
            logging.error("Remote transcriber is not available. Please install requests package and set REMOTE_TRANSCRIBER_* environment variables.")
            self.websocket.send(json.dumps({
                "uid": self.client_uid,
                "status": "ERROR",
                "message": "Remote backend is not available. Please install requests package and set REMOTE_TRANSCRIBER_* environment variables."
            }))
            self.websocket.close()
            return

        try:
            # Create a new transcriber for each client to enable concurrent requests
            self.create_model()
        except Exception as e:
            logging.error(f"Failed to initialize Remote transcriber: {e}")
            self.websocket.send(json.dumps({
                "uid": self.client_uid,
                "status": "ERROR",
                "message": f"Failed to initialize Remote transcriber: {str(e)}"
            }))
            self.websocket.close()
            return

        self.use_vad = use_vad

        # Load management: one-in-flight request per stream (LIFO approach)
        self.transcription_lock = threading.Lock()  # Protects in-flight request state
        self.transcription_in_flight = False  # True when a request is being processed

        # Sending policy: do NOT resend the whole transcript window each time.
        # We only send deltas (new completed segments + latest partial) to avoid "queueing" behavior.
        self._last_sent_completed_idx = 0
        self._last_sent_partial_fingerprint = None

        # threading
        self.trans_thread = threading.Thread(target=self.speech_to_text)
        self.trans_thread.start()
        self.websocket.send(
            json.dumps(
                {
                    "uid": self.client_uid,
                    "message": self.SERVER_READY,
                    "backend": "remote"
                }
            )
        )

    def create_model(self):
        """
        Instantiates a new Remote transcriber.
        """
        api_url = os.getenv("TRANSCRIBER_URL") or os.getenv("REMOTE_TRANSCRIBER_URL")
        api_key = (os.getenv("TRANSCRIBER_API_KEY") or os.getenv("REMOTE_TRANSCRIBER_API_KEY") or "").strip()
        # Model parameter is required by API but ignored by transcription service (uses its own configured model)
        # Use a placeholder value since the service doesn't actually use this parameter
        model = self.model or os.getenv("REMOTE_TRANSCRIBER_MODEL") or "default"
        
        if not api_url:
            raise ValueError(
                "TRANSCRIBER_URL (or REMOTE_TRANSCRIBER_URL) environment variable is not set. "
                "This is required to connect to the remote transcription service."
            )
        if not api_key:
            raise ValueError(
                "TRANSCRIBER_API_KEY (or REMOTE_TRANSCRIBER_API_KEY) environment variable is not set. "
                "This is required to authenticate with the remote transcription service."
            )
        
        # Log masked API key for debugging
        api_key_masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
        logging.debug(f"Creating RemoteTranscriber with API key: {api_key_masked}, URL: {api_url}, Model: {model}")
        
        self.transcriber = RemoteTranscriber(
            api_url=api_url,
            api_key=api_key,
            model=model,
            transcription_tier=self.transcription_tier,
            sampling_rate=self.RATE,
        )

    def transcribe_audio(self, input_sample):
        """
        Transcribes the provided audio sample using Remote API.

        Args:
            input_sample (np.array): The audio chunk to be transcribed. This should be a NumPy
                                    array representing the audio data.

        Returns:
            The transcription result from the transcriber. The exact format of this result
            depends on the implementation of the `transcriber.transcribe` method but typically
            includes the transcribed text.
        """
        # Each client has its own transcriber instance, so no lock needed for concurrent requests
        # Reduce language detection segments if language was not provided to speed up first transcription
        # Default is 10 segments (300 seconds), reduce to 1-2 segments (30-60 seconds) when auto-detecting
        language_detection_segments = 1 if not self.language_provided else int(os.getenv('LANGUAGE_DETECTION_SEGMENTS', '10'))
        result, info = self.transcriber.transcribe(
            input_sample,
            initial_prompt=self.initial_prompt,
            language=self.language,
            task=self.task,
            vad_filter=self.use_vad,
            vad_parameters=self.vad_parameters if self.use_vad else None,
            language_detection_segments=language_detection_segments)

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
        # IMPORTANT:
        # - We send only deltas (not the entire last-N window) to avoid spamming downstream.
        # - A "no segments" result from remote backend is treated like silence; we do not
        #   resend previous output here.
        if len(result):
            self.t_start = None
            last_segment = self.update_segments(result, duration)

            # New completed segments since last send
            new_completed = []
            if self._last_sent_completed_idx < len(self.transcript):
                new_completed = self.transcript[self._last_sent_completed_idx:].copy()
                self._last_sent_completed_idx = len(self.transcript)

            # Build delta payload: new completed + latest partial (if any)
            segments_to_send = new_completed
            if last_segment:
                # Avoid duplicating a completed segment that was appended to transcript.
                if not last_segment.get("completed", False):
                    # For partial segments: always include in Redis to ensure GET endpoint has current state
                    # The fingerprint check is for logging/debugging, but we always send to maintain Redis consistency
                    text = (last_segment.get("text") or "").strip()
                    start = last_segment.get("start")
                    end = last_segment.get("end")
                    fingerprint = (start, end, text, False)

                    if fingerprint != self._last_sent_partial_fingerprint:
                        segments_to_send = segments_to_send + [last_segment]
                        self._last_sent_partial_fingerprint = fingerprint
                        logging.info(
                            f"SEGMENT_UPDATE: client={self.client_uid}, partial_segment={text[:50]}, "
                            f"start={start}, end={end}"
                        )
                    else:
                        # Partial segment unchanged, but still send to Redis for GET endpoint consistency
                        # This ensures the GET transcript endpoint always sees the latest partial segment
                        segments_to_send = segments_to_send + [last_segment]
                        logging.debug(
                            f"SEGMENT_UPDATE: client={self.client_uid}, sending unchanged partial to Redis "
                            f"(start={start}, end={end}, text={text[:30]})"
                        )

            if segments_to_send:
                self.send_transcription_to_client(segments_to_send)
        else:
            # No output (common when remote VAD removes everything): don't spam previous output
            # and let the buffer advancement logic in speech_to_text move us forward.
            if self.t_start is None:
                self.t_start = time.time()

    def speech_to_text(self):
        """
        Process an audio stream in an infinite loop, continuously transcribing the speech.

        LIFO (Last-In, First-Out) approach:
        - Only one transcription request is in-flight at a time
        - Wait for response before processing next chunk
        - After each response, ALWAYS get the LATEST audio from buffer (not sequential)
        - This ensures we always transcribe the most recent audio, discarding older queued chunks

        This method continuously receives audio frames, performs real-time transcription, and sends
        transcribed segments to the client via a WebSocket connection.

        Raises:
            Exception: If there is an issue with audio processing or WebSocket communication.

        """
        while True:
            if self.exit:
                logging.info("Exiting speech to text thread")
                break

            if self.frames_np is None:
                time.sleep(0.1)
                continue

            # LIFO: Wait for any in-flight request to complete
            # The in-flight request will process the latest audio after it receives response
            with self.transcription_lock:
                if self.transcription_in_flight:
                    # Request is in-flight - wait for it to complete
                    logging.debug("LIFO: Request in-flight, waiting for response...")
                    time.sleep(0.1)  # Wait briefly before checking again
                    continue
                
                # No request in-flight - get the LATEST audio from buffer (LIFO: always latest, not sequential)
                self.clip_audio_if_no_valid_segment()
                latest_input_bytes, latest_duration = self.get_audio_chunk_for_processing()
                
                if latest_duration < self.min_audio_s:
                    # Not enough audio yet, wait for more
                    time.sleep(0.1)
                    continue
                
                # Mark as in-flight (we'll get latest audio after rate limit wait)
                self.transcription_in_flight = True
            
            # Rate limiting: ensure we don't exceed max requests per second
            # Simple check before making the request - wait if needed
            if self.min_time_between_requests > 0:
                current_time = time.time()
                time_since_last = current_time - self.last_transcription_time
                if time_since_last < self.min_time_between_requests:
                    wait_time = self.min_time_between_requests - time_since_last
                    logging.info(f"RATE_LIMIT: Waiting {wait_time:.3f}s before next transcription request (last was {time_since_last:.3f}s ago, min interval is {self.min_time_between_requests:.3f}s)")
                    time.sleep(wait_time)
                    
                    # After waiting, re-fetch the LATEST audio from buffer (LIFO: always get freshest audio)
                    # New audio may have accumulated during the wait
                    with self.transcription_lock:
                        self.clip_audio_if_no_valid_segment()
                        latest_input_bytes, latest_duration = self.get_audio_chunk_for_processing()
                        if latest_duration >= self.min_audio_s:
                            current_chunk = latest_input_bytes.copy()
                            current_duration = latest_duration
                            logging.info(f"LIFO: After rate limit wait, processing latest audio chunk (duration={latest_duration:.2f}s)")
                        else:
                            # Not enough audio after wait, clear in-flight flag and continue
                            logging.debug(f"LIFO: After rate limit wait, not enough audio (duration={latest_duration:.2f}s < min={self.min_audio_s:.2f}s)")
                            self.transcription_in_flight = False
                            continue
                else:
                    # No wait needed, use the audio we already fetched
                    with self.transcription_lock:
                        self.clip_audio_if_no_valid_segment()
                        latest_input_bytes, latest_duration = self.get_audio_chunk_for_processing()
                        if latest_duration >= self.min_audio_s:
                            current_chunk = latest_input_bytes.copy()
                            current_duration = latest_duration
                            logging.info(f"LIFO: Processing latest audio chunk (duration={latest_duration:.2f}s)")
                        else:
                            # Not enough audio, clear in-flight flag and continue
                            logging.debug(f"LIFO: Not enough audio (duration={latest_duration:.2f}s < min={self.min_audio_s:.2f}s)")
                            self.transcription_in_flight = False
                            continue
            
            # Process the chunk and wait for response
            try:
                result = self.transcribe_audio(current_chunk)
                
                # Update last request time when request completes
                self.last_transcription_time = time.time()

                # ALGORITHM A: VAD silence detection - cut buffer when no voice activity
                # Only block on language detection if language was not provided initially
                # If language was provided, we can send transcription immediately
                if result is None or (not self.language_provided and self.language is None):
                    # VAD silence: cut buffer by current_duration
                    with self.lock:
                        self.timestamp_offset += current_duration
                    logging.info(f"ALGORITHM_A: VAD silence detected (result=None or language=None), cutting buffer by {current_duration:.3f}s")
                    time.sleep(0.25)    # wait for voice activity, result is None when no voice activity
                else:
                    # If the remote backend returns an empty list of segments, treat as "silence".
                    # ALGORITHM A: VAD silence - cut buffer when no segments returned
                    try:
                        if hasattr(result, "__len__") and len(result) == 0:
                            with self.lock:
                                self.timestamp_offset += current_duration
                            logging.info(f"ALGORITHM_A: VAD silence detected (empty segments), cutting buffer by {current_duration:.3f}s")
                            time.sleep(0.25)
                        else:
                            # Process transcription result (may advance buffer if completed segments or threshold reached)
                            self.handle_transcription_output(result, current_duration)
                    except Exception:
                        # If result isn't well-formed, fall back to existing handler.
                        self.handle_transcription_output(result, current_duration)
                
                # LIFO: After response received, ALWAYS get the LATEST audio from buffer
                # This ensures we process the most recent audio, discarding any older chunks
                # Note: get_audio_chunk_for_processing uses self.lock, not transcription_lock, so safe to call
                self.clip_audio_if_no_valid_segment()
                latest_input_bytes, latest_duration = self.get_audio_chunk_for_processing()
                
                # Check if there's new audio to process
                with self.transcription_lock:
                    if latest_duration >= self.min_audio_s:
                        # New audio available - will be processed on next iteration (LIFO: latest is most important)
                        logging.info(f"LIFO: Response received, latest audio available (duration={latest_duration:.2f}s), will process on next iteration")
                        # Clear in-flight flag so next iteration processes the latest audio
                        self.transcription_in_flight = False
                    else:
                        # No new audio available, clear in-flight flag
                        logging.debug(f"LIFO: Response received, no new audio available (duration={latest_duration:.2f}s < min={self.min_audio_s:.2f}s)")
                        self.transcription_in_flight = False

            except Exception as e:
                # Treat remote overload as a normal backpressure signal (not an error):
                # release the in-flight flag so we can attempt again with a newer/larger audio window.
                if RemoteTranscriberOverloaded is not None and isinstance(e, RemoteTranscriberOverloaded):
                    retry_after = getattr(e, "retry_after_s", 0.5) or 0.5
                    logging.info(f"Remote transcriber overloaded; backing off {retry_after:.2f}s (HTTP {getattr(e, 'status_code', '??')})")
                    with self.transcription_lock:
                        self.transcription_in_flight = False
                    time.sleep(min(float(retry_after), 2.0))
                else:
                    logging.error(f"[ERROR]: Failed to transcribe audio chunk: {e}")
                    with self.transcription_lock:
                        self.transcription_in_flight = False
                    time.sleep(0.1)  # Brief backoff on error

    def format_segment(self, start, end, text, completed=False, language=None):
        """
        Formats a transcription segment with precise start and end times alongside the transcribed text.

        Args:
            start (float): The start time of the transcription segment in seconds.
            end (float): The end time of the transcription segment in seconds.
            text (str): The transcribed text corresponding to the segment.
            completed (bool): Whether the segment is completed or partial.
            language (str): The detected language for this segment.

        Returns:
            dict: A dictionary representing the formatted transcription segment, including
                'start' and 'end' times as strings with three decimal places, the 'text'
                of the transcription, 'completed' status, and 'language' if provided.
        """
        segment = {
            'start': "{:.3f}".format(start),
            'end': "{:.3f}".format(end),
            'text': text,
            'completed': completed
        }
        
        # Add language if provided
        if language is not None:
            segment['language'] = language
            
        return segment

    def update_segments(self, segments, duration):
        """
        Processes the segments from Remote API. Appends all the segments to the list
        except for the last segment assuming that it is incomplete.

        Updates the ongoing transcript with transcribed segments, including their start and end times.
        Complete segments are appended to the transcript in chronological order. Incomplete segments
        (assumed to be the last one) are processed to identify repeated content. If the same incomplete
        segment is seen multiple times, it updates the offset and appends the segment to the transcript.
        A threshold is used to detect repeated content and ensure it is only included once in the transcript.
        The timestamp offset is updated based on the duration of processed segments. The method returns the
        last processed segment, allowing it to be sent to the client for real-time updates.

        Args:
            segments(Iterable[Segment]) : iterable of segments as returned by Remote API
            duration(float): duration of the current chunk

        Returns:
            dict or None: The last processed segment with its start time, end time, and transcribed text.
                     Returns None if there are no valid segments to process.
        """
        # Convert iterable to list if needed
        segments_list = list(segments) if not isinstance(segments, list) else segments
        
        offset = None
        self.current_out = ''
        last_segment = None

        # process complete segments
        if len(segments_list) > 1 and segments_list[-1].no_speech_prob <= self.no_speech_thresh:
            for i, s in enumerate(segments_list[:-1]):
                text_ = s.text

                # Update circuit-breaker timestamp BEFORE filtering, so hallucinations still count as activity
                try:
                    if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                        self.collector_client.server_ref.server_last_transcription_ts = time.time()
                except Exception:
                    pass


                # Apply hallucination filter
                filtered_text = self._filter_hallucinations(text_)
                if filtered_text is None:
                    # Log and skip this segment if it's a hallucination
                    try:
                        if WL_LOG_HALLUCINATIONS:
                            logger.info(f'HALLUCINATION_FILTERED: "{text_}"')
                    except Exception:
                        pass
                    continue
                
                self.text.append(filtered_text)
                with self.lock:
                    start, end = self.timestamp_offset + s.start, self.timestamp_offset + min(duration, s.end)

                if start >= end:
                    continue
                if s.no_speech_prob > self.no_speech_thresh:
                    continue

                self.transcript.append(self.format_segment(start, end, filtered_text, completed=True, language=self.language))
                # Advance by the end of the last complete segment
                offset = min(duration, s.end)

        # only process the last segment if it satisfies the no_speech_thresh
        if len(segments_list) > 0 and segments_list[-1].no_speech_prob <= self.no_speech_thresh:
            # Update circuit-breaker timestamp BEFORE filtering for the last (partial) segment
            try:
                if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                    self.collector_client.server_ref.server_last_transcription_ts = time.time()
            except Exception:
                pass

            # Apply hallucination filter to the current output
            filtered_current_out = self._filter_hallucinations(segments_list[-1].text)
            if filtered_current_out is not None:
                self.current_out += filtered_current_out
                with self.lock:
                    last_segment = self.format_segment(
                        self.timestamp_offset + segments_list[-1].start,
                        self.timestamp_offset + min(duration, segments_list[-1].end),
                        self.current_out,
                        completed=False,
                        language=self.language
                    )
            else:
                # Log and skip this segment if it's a hallucination
                try:
                    if WL_LOG_HALLUCINATIONS:
                        logger.info(f'HALLUCINATION_FILTERED: "{segments_list[-1].text}"')
                except Exception:
                    pass
                last_segment = None

        if self.current_out.strip() == self.prev_out.strip() and self.current_out != '':
            self.same_output_count += 1

            # if we remove the audio because of same output on the nth reptition we might remove the 
            # audio thats not yet transcribed so, capturing the time when it was repeated for the first time
            if self.end_time_for_same_output is None:
                self.end_time_for_same_output = segments_list[-1].end if segments_list else duration
            time.sleep(0.1)     # wait for some voice activity just in case there is an unitended pause from the speaker for better punctuations.
        else:
            self.same_output_count = 0
            self.end_time_for_same_output = None

        # if same incomplete segment is seen multiple times then update the offset
        # and append the segment to the list
        if self.same_output_count > self.same_output_threshold:
            if not len(self.text) or self.text[-1].strip().lower() != self.current_out.strip().lower():
                # Update circuit-breaker timestamp BEFORE filtering repeated incomplete output
                try:
                    if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                        self.collector_client.server_ref.server_last_transcription_ts = time.time()
                except Exception:
                    pass
                
                # Apply hallucination filter
                filtered_current_out = self._filter_hallucinations(self.current_out)
                if filtered_current_out is not None:
                    self.text.append(filtered_current_out)
                    with self.lock:
                        start, end = self.timestamp_offset, self.timestamp_offset + min(duration, self.end_time_for_same_output or duration)
                    if start < end:
                        # Create completed segment and add to transcript
                        completed_segment = self.format_segment(start, end, filtered_current_out, completed=True, language=self.language)
                        self.transcript.append(completed_segment)
                        # Return the completed segment so it's sent to client immediately
                        # This ensures the dashboard sees the segment transition from partial to completed
                        last_segment = completed_segment
                        logging.info(f"SAME_OUTPUT_THRESHOLD: client={self.client_uid}, completed_segment={filtered_current_out[:50]}, start={start:.3f}, end={end:.3f}, count={self.same_output_count}")
                    # Advance by the reconfirmed segment end time
                    offset = min(duration, self.end_time_for_same_output or duration)
                else:
                    # Log filtered repeated hallucination
                    try:
                        if WL_LOG_HALLUCINATIONS:
                            logger.info(f'HALLUCINATION_FILTERED: "{self.current_out}"')
                    except Exception:
                        pass
                    last_segment = None
            else:
                # Text is the same as last completed segment, don't add duplicate
                last_segment = None
            self.current_out = ''
            # Advance by the reconfirmed segment end time, or full duration if not set
            if self.end_time_for_same_output:
                offset = min(duration, self.end_time_for_same_output)
            else:
                offset = duration
            self.same_output_count = 0
            self.end_time_for_same_output = None
        else:
            self.prev_out = self.current_out

        # ALGORITHM A: Only advance/cut buffer when:
        # 1. Completed segments were added (offset set at line 3296)
        # 2. SAME_OUTPUT_THRESHOLD confirmed (offset set at lines 3366, 3380-3383)
        # 3. VAD silence is handled separately in speech_to_text() (line 3155)
        # 
        # If offset is None, we do NOT advance - this means:
        # - No completed segments
        # - SAME_OUTPUT_THRESHOLD not reached
        # - Buffer stays as-is, will be re-transcribed on next iteration with more audio
        if offset is not None:
            with self.lock:
                self.timestamp_offset += offset
                threshold_reached = self.same_output_count > self.same_output_threshold
                logging.debug(f"ALGORITHM_A: Advanced timestamp_offset by {offset:.3f}s (reason={'SAME_OUTPUT_THRESHOLD' if threshold_reached else 'completed_segments'})")
        else:
            # No advancement - buffer will accumulate more audio and be re-transcribed
            logging.debug(f"ALGORITHM_A: No offset advancement (no completed segments, threshold not reached, buffer will accumulate)")

        return last_segment

    def set_language(self, info):
        """
        Sets the language for the client based on transcription info.

        Args:
            info: TranscriptionInfo object containing language information.
        """
        if info and hasattr(info, 'language'):
            self.language = info.language
            lang_prob = getattr(info, 'language_probability', 1.0)
            self.websocket.send(json.dumps({
                "uid": self.client_uid,
                "language": self.language,
                "language_prob": lang_prob
            }))
            logger.info(f"LANGUAGE_DETECTION: client={self.client_uid}, language={self.language}, confidence={lang_prob:.4f}")


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
