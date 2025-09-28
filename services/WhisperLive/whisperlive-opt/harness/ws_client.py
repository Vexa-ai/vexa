"""
WebSocket Client for WhisperLive Load Testing

Streams audio frames to WhisperLive server and collects real-time metrics
including transcript events, latency measurements, and connection statistics.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, AsyncGenerator, Any
import numpy as np
import librosa
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)


@dataclass
class TranscriptEvent:
    """Represents a transcript event from WhisperLive."""
    timestamp: float
    text: str
    is_final: bool = False
    segment_id: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None


@dataclass
class ConnectionMetrics:
    """Per-connection metrics tracking."""
    conn_id: str
    meeting_label: str
    sample_id: str
    
    # Audio streaming
    audio_path: str
    audio_data: np.ndarray
    frame_size: int
    frame_ms: int
    
    # Timing
    start_time: float = 0.0
    last_frame_sent: float = 0.0
    
    # Counters
    frames_sent: int = 0
    frames_dropped: int = 0
    transcript_events: int = 0
    
    # Latency tracking
    latency_samples: deque = field(default_factory=lambda: deque(maxlen=100))
    
    # Transcript collection
    transcript_events_list: List[TranscriptEvent] = field(default_factory=list)
    final_transcript: str = ""
    
    # 10-second sliding window counter
    window_start: float = 0.0
    window_events: deque = field(default_factory=lambda: deque(maxlen=1000))
    
    def get_c10s(self, current_time: float) -> int:
        """Get count of transcript events in last 10 seconds."""
        cutoff = current_time - 10.0
        return sum(1 for event in self.window_events if event.timestamp >= cutoff)
    
    def get_rate_per_s(self) -> float:
        """Get transcript events per second rate."""
        if not self.start_time:
            return 0.0
        elapsed = time.time() - self.start_time
        return self.transcript_events / max(elapsed, 1.0)
    
    def get_avg_latency(self) -> float:
        """Get average latency in seconds."""
        if not self.latency_samples:
            return 0.0
        return np.mean(list(self.latency_samples))
    
    def get_p95_latency(self) -> float:
        """Get 95th percentile latency in seconds."""
        if not self.latency_samples:
            return 0.0
        return np.percentile(list(self.latency_samples), 95)
    
    def get_drop_rate(self) -> float:
        """Get frame drop rate as percentage."""
        total_frames = self.frames_sent + self.frames_dropped
        if total_frames == 0:
            return 0.0
        return (self.frames_dropped / total_frames) * 100.0


class WhisperLiveClient:
    """Async WebSocket client for WhisperLive server."""
    
    def __init__(self, 
                 conn_id: str,
                 meeting_label: str,
                 sample_id: str,
                 audio_path: str,
                 ws_url: str,
                 frame_ms: int = 20,
                 language: str = "en",
                 model: str = "small",
                 auth_header: Optional[str] = None):
        
        self.conn_id = conn_id
        self.meeting_label = meeting_label
        self.sample_id = sample_id
        self.ws_url = ws_url
        self.frame_ms = frame_ms
        self.language = language
        self.model = model
        self.auth_header = auth_header
        
        # Load audio data
        self.audio_data = self._load_audio(audio_path)
        self.frame_size = int(16000 * frame_ms / 1000)  # 16kHz, frame_ms
        
        # Initialize metrics
        self.metrics = ConnectionMetrics(
            conn_id=conn_id,
            meeting_label=meeting_label,
            sample_id=sample_id,
            audio_path=audio_path,
            audio_data=self.audio_data,
            frame_size=self.frame_size,
            frame_ms=frame_ms
        )
        
        # Connection state
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.is_connected = False
        self.is_streaming = False
        
    def _load_audio(self, audio_path: str) -> np.ndarray:
        """Load and prepare audio for streaming."""
        try:
            # Load audio at 16kHz mono
            audio, sr = librosa.load(audio_path, sr=16000, mono=True)
            
            # Convert to float32 for WebSocket transmission (server expects float32)
            audio_float32 = audio.astype(np.float32)
            
            logger.info(f"Loaded audio: {audio_path} ({len(audio_float32)} samples, {len(audio_float32)/sr:.1f}s)")
            return audio_float32
            
        except Exception as e:
            logger.error(f"Error loading audio {audio_path}: {e}")
            raise
            
    async def connect(self) -> bool:
        """Connect to WhisperLive server."""
        try:
            headers = {}
            if self.auth_header:
                key, value = self.auth_header.split(':', 1)
                headers[key.strip()] = value.strip()
                
            self.websocket = await websockets.connect(
                self.ws_url,
                extra_headers=headers,
                ping_interval=30,
                ping_timeout=10
            )
            
            # Send initialization message with required fields
            init_msg = {
                "uid": self.conn_id,
                "language": self.language,
                "task": "transcribe",
                "model": self.model,
                "platform": "optimization_test",
                "meeting_url": f"test://meeting/{self.conn_id}",
                "token": "test_token",
                "meeting_id": f"meeting_{self.conn_id}"
            }
            
            await self.websocket.send(json.dumps(init_msg))
            self.is_connected = True
            self.metrics.start_time = time.time()
            self.metrics.window_start = time.time()
            
            logger.info(f"Connected {self.conn_id} to {self.ws_url}")
            return True
            
        except Exception as e:
            logger.error(f"Connection failed for {self.conn_id}: {e}")
            return False
            
    async def disconnect(self):
        """Disconnect from server."""
        self.is_connected = False
        self.is_streaming = False
        
        if self.websocket:
            try:
                # Set a timeout for the close operation to prevent hanging
                await asyncio.wait_for(self.websocket.close(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"WebSocket close timeout for {self.conn_id}")
            except Exception as e:
                logger.debug(f"WebSocket close error for {self.conn_id}: {e}")
            finally:
                self.websocket = None
        
    async def _send_audio_frames(self) -> AsyncGenerator[bool, None]:
        """Send audio frames at specified rate."""
        frame_interval = self.frame_ms / 1000.0  # Convert to seconds
        
        try:
            for i in range(0, len(self.audio_data), self.frame_size):
                if not self.is_connected or not self.websocket:
                    break
                    
                chunk = self.audio_data[i:i+self.frame_size]
                
                # Pad if necessary
                if len(chunk) < self.frame_size:
                    chunk = np.pad(chunk, (0, self.frame_size - len(chunk)), mode='constant')
                
                try:
                    await self.websocket.send(chunk.tobytes())
                    self.metrics.frames_sent += 1
                    self.metrics.last_frame_sent = time.time()
                    
                except Exception as e:
                    logger.warning(f"Failed to send frame for {self.conn_id}: {e}")
                    self.metrics.frames_dropped += 1
                    
                # Wait for next frame
                await asyncio.sleep(frame_interval)
                
        except Exception as e:
            logger.error(f"Audio streaming error for {self.conn_id}: {e}")
            
        yield False  # Indicate streaming complete
        
    async def _receive_transcripts(self) -> AsyncGenerator[TranscriptEvent, None]:
        """Receive transcript events from server."""
        try:
            while self.is_connected and self.websocket:
                try:
                    message = await asyncio.wait_for(
                        self.websocket.recv(), 
                        timeout=1.0
                    )
                    
                    if isinstance(message, str):
                        data = json.loads(message)
                        
                        # Handle WhisperLive server format: {"uid": "conn_00", "segments": [...]}
                        if 'segments' in data and data.get('uid') == self.conn_id:
                            segments = data.get('segments', [])
                            
                            for segment in segments:
                                # Extract text from segment
                                text = segment.get('text', '').strip()
                                if not text:
                                    continue
                                    
                                # Determine if segment is final (completed)
                                is_final = segment.get('completed', False)
                                
                                event = TranscriptEvent(
                                    timestamp=time.time(),
                                    text=text,
                                    is_final=is_final,
                                    segment_id=segment.get('id'),
                                    start_time=segment.get('start'),
                                    end_time=segment.get('end')
                                )
                                
                                # Update metrics
                                self.metrics.transcript_events += 1
                                self.metrics.transcript_events_list.append(event)
                                self.metrics.window_events.append(event)
                                
                                # Estimate latency (rough approximation)
                                if self.metrics.last_frame_sent > 0:
                                    latency = event.timestamp - self.metrics.last_frame_sent
                                    if 0 < latency < 10.0:  # Reasonable latency range
                                        self.metrics.latency_samples.append(latency)
                                
                                yield event
                            
                except asyncio.TimeoutError:
                    continue
                except ConnectionClosed:
                    break
                except Exception as e:
                    logger.warning(f"Receive error for {self.conn_id}: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Transcript receiving error for {self.conn_id}: {e}")
            
    async def stream_audio(self, duration: Optional[float] = None) -> List[TranscriptEvent]:
        """Stream audio and collect transcripts for specified duration."""
        if not self.is_connected:
            logger.error(f"Not connected: {self.conn_id}")
            return []
            
        self.is_streaming = True
        events = []
        
        try:
            # Start audio streaming and transcript receiving concurrently
            audio_task = asyncio.create_task(
                self._send_audio_frames().__anext__()
            )
            
            # Collect transcripts
            transcript_task = asyncio.create_task(
                self._collect_transcripts(duration)
            )
            
            # Wait for completion
            await asyncio.gather(audio_task, transcript_task, return_exceptions=True)
            
            # Get collected events
            events = self.metrics.transcript_events_list.copy()
            
        except Exception as e:
            logger.error(f"Streaming error for {self.conn_id}: {e}")
        finally:
            self.is_streaming = False
            
        return events
        
    async def _collect_transcripts(self, duration: Optional[float] = None):
        """Collect transcripts for specified duration."""
        start_time = time.time()
        
        try:
            async for event in self._receive_transcripts():
                if duration and (time.time() - start_time) >= duration:
                    break
                    
        except Exception as e:
            logger.error(f"Transcript collection error for {self.conn_id}: {e}")
            
    def finalize_transcript(self) -> str:
        """Merge final transcript from collected events."""
        # Sort events by timestamp and merge finalized segments
        events = sorted(
            [e for e in self.metrics.transcript_events_list if e.is_final],
            key=lambda x: x.timestamp
        )
        
        # Merge text
        transcript_parts = []
        for event in events:
            if event.text.strip():
                transcript_parts.append(event.text.strip())
                
        final_transcript = ' '.join(transcript_parts)
        self.metrics.final_transcript = final_transcript
        
        return final_transcript
        
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get current metrics summary."""
        current_time = time.time()
        
        return {
            'conn_id': self.conn_id,
            'meeting_label': self.meeting_label,
            'sample_id': self.sample_id,
            'is_connected': self.is_connected,
            'is_streaming': self.is_streaming,
            'frames_sent': self.metrics.frames_sent,
            'frames_dropped': self.metrics.frames_dropped,
            'transcript_events': self.metrics.transcript_events,
            'c10s': self.metrics.get_c10s(current_time),
            'rate_per_s': self.metrics.get_rate_per_s(),
            'avg_latency': self.metrics.get_avg_latency(),
            'p95_latency': self.metrics.get_p95_latency(),
            'drop_rate': self.metrics.get_drop_rate(),
            'final_transcript': self.metrics.final_transcript
        }


async def create_client_pool(config: Dict[str, Any], 
                           audio_samples: List[Dict[str, str]]) -> List[WhisperLiveClient]:
    """Create a pool of WebSocket clients."""
    clients = []
    
    for i, sample in enumerate(audio_samples):
        conn_id = f"conn_{i:02d}"
        meeting_label = f"meeting_{sample['sample_id']}"
        
        client = WhisperLiveClient(
            conn_id=conn_id,
            meeting_label=meeting_label,
            sample_id=sample['sample_id'],
            audio_path=sample['audio_path'],
            ws_url=config['server']['ws_url'],
            frame_ms=config['run']['frame_ms'],
            language=config['server'].get('language', 'en'),
            model=config['server'].get('model', 'small'),
            auth_header=config['server'].get('auth_header')
        )
        
        clients.append(client)
        
    return clients


async def connect_clients(clients: List[WhisperLiveClient], 
                        max_concurrent: int = 10) -> List[WhisperLiveClient]:
    """Connect clients with controlled concurrency."""
    connected_clients = []
    
    # Connect in batches to avoid overwhelming the server
    for i in range(0, len(clients), max_concurrent):
        batch = clients[i:i+max_concurrent]
        
        # Connect batch concurrently
        tasks = [client.connect() for client in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Add successfully connected clients
        for client, success in zip(batch, results):
            if success is True:
                connected_clients.append(client)
            else:
                logger.error(f"Failed to connect {client.conn_id}: {success}")
                
        # Small delay between batches
        if i + max_concurrent < len(clients):
            await asyncio.sleep(0.5)
            
    logger.info(f"Connected {len(connected_clients)}/{len(clients)} clients")
    return connected_clients
