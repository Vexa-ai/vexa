import os
import time
import threading
import json
import functools
import logging
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

# Import for health check HTTP server
import http.server
import socketserver
import threading

# Import Redis
import redis
import uuid

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
            
            message = {
                # Per current structure, the whole payload is JSON dumped into one field
                "payload": json.dumps(payload) 
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

        # --- Capacity configuration (WL_MAX_CLIENTS, default 10) ---
        try:
            self.config_max_clients = int(os.getenv("WL_MAX_CLIENTS", "10"))
        except Exception:
            self.config_max_clients = 10
        logging.info(f"CONFIG: max_clients set to {self.config_max_clients} (env WL_MAX_CLIENTS)")

        # --- WL Scaling: publish live session count & heartbeat ---
        self._wl_redis = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
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
        logging.info(f"📡 THIS URL WILL BE REGISTERED TO REDIS: {self._ws_url}")
        self._metric_stop_evt = threading.Event()
        threading.Thread(target=self._metric_heartbeat, daemon=True).start()
        # --- End WL Scaling block ---

    # --- WL Scaling helper methods (class level) ---
    def _metric_heartbeat(self):
        """Background timer that refreshes Redis score + heartbeat every 15 s."""
        while not self._metric_stop_evt.is_set():
            self._publish_sessions_metric()
            self._metric_stop_evt.wait(15)

    def _publish_sessions_metric(self):
        """Update wl:rank sorted-set score and wl:hb:<url> heartbeat key."""
        try:
            current = len(self.client_manager.clients) if self.client_manager else 0
            pipe = self._wl_redis.pipeline()
            pipe.zadd("wl:rank", {self._ws_url: current})
            pipe.setex(f"wl:hb:{self._ws_url}", 35, 1)
            pipe.execute()
        except Exception as exc:
            logging.warning(f"WhisperLive metric publish failed: {exc}")
    # --- End WL Scaling helper methods ---

    def initialize_client(
        self, websocket, options, faster_whisper_custom_model_path,
        whisper_tensorrt_path, trt_multilingual
    ):
        """
        Initializes a client based on the backend type.
        """
        if options is None:
            options = {}
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
                collector_client_ref=self.collector_client,
                server_options=self.server_options
            )
        self.client_manager.add_client(websocket, client)
        # Update Redis metric after new client joins
        self._publish_sessions_metric()

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

        # For the health check, we need to know if Redis is being used.
        # This is inferred from the presence of the REDIS_STREAM_URL env var.
        redis_url_for_health_check = os.getenv("REDIS_STREAM_URL")
        if redis_url_for_health_check:
            self.start_health_check_server(host, 9091)

        logger.info(f"SERVER_START: host={host}, port={port}, backend={self.backend.value}, single_model={single_model}")
        
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
            
            # Immediately publish to Redis so clients can discover this server
            self._publish_sessions_metric()
            logger.info(f"REDIS_PUBLISH: Server published to Redis immediately on startup")
            
            # Start self-monitoring thread
            if self.self_monitor_thread is None:
                self._stop_self_monitor.clear()
                self.self_monitor_thread = threading.Thread(target=self._self_monitor, daemon=True)
                self.self_monitor_thread.start()
                logger.info(f"SELF_MONITOR: Started self-monitoring thread. Interval: {self.health_monitor_interval}s, Max Streak: {self.max_unhealthy_streak}")

            server.serve_forever()

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
        # Update Redis metric after client leaves
        self._publish_sessions_metric()

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
            
            # Log with client UID if available
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
        
        logging.info(
            f"Processing Speaker Activity Update for UID {uid_for_log}: Type='{event_type}', Name='{participant_name}', RelativeTs={relative_ts}ms (Client on record: {client.client_uid if client else 'N/A_CLIENT_FALLBACK'})" # CORRECTED
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
                 collector_client_ref: Optional[TranscriptionCollectorClient] = None,
                 server_options: Optional[dict] = None):
        self.websocket = websocket
        self.language = language
        self.task = task
        self.client_uid = client_uid or str(uuid.uuid4())
        self.platform = platform
        self.meeting_url = meeting_url
        self.token = token
        self.meeting_id = meeting_id
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
            
            # Log the transcription data to file with more detailed formatting
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
                    
            logger.info(f"TRANSCRIPTION: client={self.client_uid}, platform={self.platform}, meeting_url={self.meeting_url}, token={self.token}, meeting_id={self.meeting_id}, segments=\n" + "\n".join(formatted_segments))
            # Update server-level last transcription timestamp for circuit breaker
            try:
                # Access the parent TranscriptionServer via collector_client if available
                # We do not have a direct reference here; use global update via Redis ping thread owner
                # Instead, use a lightweight process-wide marker
                from time import time as _now
                # Store on the TranscriptionServer instance through the collector reference if possible
                if self.collector_client and hasattr(self.collector_client, 'server_ref') and self.collector_client.server_ref:
                    self.collector_client.server_ref.server_last_transcription_ts = _now()
                else:
                    # Fallback: set a module-level variable; self-monitor reads instance field primarily
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
                 collector_client_ref: Optional[TranscriptionCollectorClient] = None,
                 server_options: Optional[dict] = None):
        super().__init__(websocket, language, task, client_uid, platform, meeting_url, token, meeting_id,
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
                logging.info(f"[WhisperTensorRT:] Processing audio with duration: {duration}")
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
                 collector_client_ref: Optional[TranscriptionCollectorClient] = None,
                 server_options: Optional[dict] = None):
        super().__init__(websocket, language, task, client_uid, platform, meeting_url, token, meeting_id,
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
        self.language = "en" if self.model_size_or_path.endswith("en") else language
        self.task = task
        self.initial_prompt = initial_prompt

        server_options = server_options or {}
        self.min_audio_s = server_options.get("min_audio_s", 1.0)
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
            if duration < self.min_audio_s:
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
