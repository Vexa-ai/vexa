"""
Test Orchestrator for WhisperLive Optimization

Manages the complete test lifecycle: warmup, steady-state detection,
main test run, and cooldown phases with comprehensive metrics collection.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any, AsyncGenerator, Iterator
import json
import numpy as np

from .ws_client import WhisperLiveClient, create_client_pool, connect_clients
from .metrics import MetricsCollector, MetricsWriter
from .logging_live import LiveDashboard

logger = logging.getLogger(__name__)


@dataclass
class TestConfig:
    """Test configuration parameters."""
    # Server settings
    ws_url: str
    language: str = "en"
    model: str = "small"
    auth_header: Optional[str] = None
    
    # Run parameters
    concurrency: int = 32
    frame_ms: int = 20
    warmup_s: int = 20
    run_s: int = 120
    cooldown_s: int = 10
    
    # Audio settings
    repeat_audio: bool = True
    shuffle_audio: bool = True
    per_conn_seed: bool = True
    
    # Metrics settings
    lambda_penalty: float = 0.5
    latency_slo: float = 2.0
    drop_slo: float = 0.02
    gpu_sample_interval: float = 1.0
    
    # Data settings
    manifest_path: str = "data/manifest.csv"


@dataclass
class TestResults:
    """Complete test results."""
    config: TestConfig
    start_time: float
    end_time: float
    duration: float
    
    # Steady state detection
    steady_state_reached: bool
    steady_state_time: Optional[float]
    steady_state_score: float
    
    # Final metrics
    final_metrics: Dict[str, Any]
    
    # Per-second metrics history
    metrics_history: List[Dict[str, Any]]
    
    # Connection results
    connection_results: Dict[str, Dict[str, Any]]
    
    # Output paths
    output_dir: Path
    per_second_csv: Path
    per_conn_csv: Path
    transcripts_dir: Path
    quality_simple_json: Optional[Path]
    judge_llm_json: Optional[Path]
    summary_md: Path


class TestCollector:
    """Orchestrates complete WhisperLive optimization test."""
    
    def __init__(self, config: TestConfig, output_dir: Path):
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.metrics_collector = MetricsCollector(
            lambda_penalty=config.lambda_penalty,
            latency_slo=config.latency_slo,
            drop_slo=config.drop_slo,
            gpu_sample_interval=config.gpu_sample_interval
        )
        
        # Client management
        self.clients: List[WhisperLiveClient] = []
        self.connection_results: Dict[str, Dict[str, Any]] = {}
        
        # Test state
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.steady_state_start: Optional[float] = None
        
        # Audio samples
        self.audio_samples: List[Dict[str, str]] = []
        
    def load_manifest(self) -> List[Dict[str, str]]:
        """Load audio manifest from CSV file."""
        import pandas as pd
        
        try:
            manifest_df = pd.read_csv(self.config.manifest_path)
            samples = []
            
            for _, row in manifest_df.iterrows():
                sample = {
                    'sample_id': str(row['sample_id']),
                    'audio_path': str(row['audio_path']),
                    'golden_path': str(row['golden_path']),
                    'lang': str(row.get('lang', 'en')),
                    'title': str(row.get('title', '')),
                    'url': str(row.get('url', ''))
                }
                samples.append(sample)
                
            logger.info(f"Loaded {len(samples)} audio samples from manifest")
            return samples
            
        except Exception as e:
            logger.error(f"Error loading manifest {self.config.manifest_path}: {e}")
            return []
            
    def prepare_audio_samples(self) -> List[Dict[str, str]]:
        """Prepare audio samples for testing."""
        samples = self.load_manifest()
        
        if not samples:
            logger.error("No audio samples available")
            return []
            
        # Handle audio repetition and shuffling
        if self.config.repeat_audio and len(samples) < self.config.concurrency:
            # Repeat samples to match concurrency
            repeated_samples = []
            for i in range(self.config.concurrency):
                sample = samples[i % len(samples)].copy()
                sample['sample_id'] = f"{sample['sample_id']}_{i}"
                repeated_samples.append(sample)
            samples = repeated_samples
            
        # Shuffle if requested
        if self.config.shuffle_audio:
            import random
            if self.config.per_conn_seed:
                random.seed(42)  # Fixed seed for reproducibility
            random.shuffle(samples)
            
        # Limit to concurrency
        samples = samples[:self.config.concurrency]
        
        logger.info(f"Prepared {len(samples)} audio samples for {self.config.concurrency} connections")
        return samples
        
    async def initialize_clients(self) -> bool:
        """Initialize and connect WebSocket clients."""
        try:
            # Prepare audio samples
            self.audio_samples = self.prepare_audio_samples()
            if not self.audio_samples:
                return False
                
            # Create client pool
            server_config = {
                'server': {
                    'ws_url': self.config.ws_url,
                    'language': self.config.language,
                    'model': self.config.model,
                    'auth_header': self.config.auth_header
                },
                'run': {
                    'frame_ms': self.config.frame_ms
                }
            }
            
            self.clients = await create_client_pool(server_config, self.audio_samples)
            
            # Connect clients with controlled concurrency
            self.clients = await connect_clients(self.clients, max_concurrent=10)
            
            if not self.clients:
                logger.error("No clients connected successfully")
                return False
                
            logger.info(f"Successfully connected {len(self.clients)} clients")
            return True
            
        except Exception as e:
            logger.error(f"Client initialization error: {e}")
            return False
            
    async def start_audio_streaming(self) -> None:
        """Start audio streaming on all clients."""
        streaming_tasks = []
        
        for client in self.clients:
            # Calculate streaming duration (warmup + run + cooldown)
            total_duration = self.config.warmup_s + self.config.run_s + self.config.cooldown_s
            task = asyncio.create_task(client.stream_audio(duration=total_duration))
            streaming_tasks.append(task)
            
        # Start all streams with small jitter to avoid thundering herd
        for i, task in enumerate(streaming_tasks):
            await asyncio.sleep(i * 0.1)  # 100ms jitter between clients
            asyncio.create_task(self._monitor_streaming(task, self.clients[i]))
            
        logger.info(f"Started audio streaming on {len(self.clients)} clients")
        
    async def _monitor_streaming(self, streaming_task: asyncio.Task, client: WhisperLiveClient):
        """Monitor individual client streaming."""
        try:
            await streaming_task
            logger.debug(f"Streaming completed for {client.conn_id}")
        except Exception as e:
            logger.error(f"Streaming error for {client.conn_id}: {e}")
            
    def collect_connection_metrics(self) -> Dict[str, Any]:
        """Collect current metrics from all connections."""
        for client in self.clients:
            metrics = client.get_metrics_summary()
            self.metrics_collector.add_connection(client.conn_id, metrics)
            
        return self.metrics_collector.connections.copy()
        
    def generate_state_stream(self, metrics_writer=None) -> Iterator[Dict[str, Any]]:
        """Generate state updates for dashboard."""
        total_seconds = self.config.warmup_s + self.config.run_s + self.config.cooldown_s
        
        for t_rel in range(total_seconds + 1):
            current_time = time.time()
            
            # Collect connection metrics
            connections = self.collect_connection_metrics()
            
            # Aggregate metrics
            metrics = self.metrics_collector.aggregate_metrics(current_time, t_rel)
            
            # Write metrics to CSV files
            if metrics_writer:
                metrics_writer.write_per_second_metrics(metrics, self.config.lambda_penalty)
                metrics_writer.write_per_connection_metrics(connections, current_time)
                metrics_writer.write_jsonl_metrics(metrics)
            
            # Check steady state
            steady_state = self.metrics_collector.is_steady_state()
            if steady_state and self.steady_state_start is None:
                self.steady_state_start = current_time
                logger.info("Steady state reached")
                
            # Prepare state for dashboard
            state = {
                't_rel': t_rel,
                'ws_url': self.config.ws_url,
                'N': len(self.clients),
                'active': len([c for c in connections.values() if c.get('is_connected', False)]),
                'frame_ms': self.config.frame_ms,
                'warmup_s': self.config.warmup_s,
                'run_s': self.config.run_s,
                'mu': metrics.mu,
                'sigma': metrics.sigma,
                'J': metrics.J,
                'lambda': self.config.lambda_penalty,
                'penalties': metrics.penalties,
                'p50': metrics.p50_latency,
                'p95': metrics.p95_latency,
                'drops': metrics.drops_percent,
                'gpu_util': metrics.gpu_util,
                'vram_mb': metrics.vram_mb,
                'top_connections': metrics.top_connections,
                'steady_state': steady_state
            }
            
            yield state
            
            # Sleep until next second
            time.sleep(1.0)
            
    async def run_test_phase(self, 
                           phase_name: str,
                           duration: int,
                           dashboard: Optional[LiveDashboard] = None,
                           metrics_writer=None) -> None:
        """Run a specific test phase."""
        logger.info(f"Starting {phase_name} phase ({duration}s)")
        
        start_time = time.time()
        end_time = start_time + duration
        
        while time.time() < end_time:
            # Collect metrics
            connections = self.collect_connection_metrics()
            current_time = time.time()
            t_rel = int(current_time - start_time)
            metrics = self.metrics_collector.aggregate_metrics(current_time, t_rel)
            
            # Write metrics to CSV files
            if metrics_writer:
                metrics_writer.write_per_second_metrics(metrics, self.config.lambda_penalty)
                metrics_writer.write_per_connection_metrics(connections, current_time)
                metrics_writer.write_jsonl_metrics(metrics)
            
            # Update dashboard if provided
            if dashboard:
                state = {
                    't_rel': t_rel,
                    'ws_url': self.config.ws_url,
                    'N': len(self.clients),
                    'active': len([c for c in connections.values() if c.get('is_connected', False)]),
                    'frame_ms': self.config.frame_ms,
                    'warmup_s': self.config.warmup_s,
                    'run_s': self.config.run_s,
                    'mu': metrics.mu,
                    'sigma': metrics.sigma,
                    'J': metrics.J,
                    'lambda': self.config.lambda_penalty,
                    'penalties': metrics.penalties,
                    'p50': metrics.p50_latency,
                    'p95': metrics.p95_latency,
                    'drops': metrics.drops_percent,
                    'gpu_util': metrics.gpu_util,
                    'vram_mb': metrics.vram_mb,
                    'top_connections': metrics.top_connections,
                    'steady_state': self.metrics_collector.is_steady_state()
                }
                
                # Update dashboard (this would need to be adapted for real-time updates)
                
            await asyncio.sleep(1.0)
            
        logger.info(f"Completed {phase_name} phase")
        
    async def finalize_connections(self) -> None:
        """Finalize all connections and collect final results."""
        for client in self.clients:
            try:
                # Get final transcript
                final_transcript = client.finalize_transcript()
                
                # Disconnect
                await client.disconnect()
                
                # Store results
                self.connection_results[client.conn_id] = {
                    'conn_id': client.conn_id,
                    'meeting_label': client.meeting_label,
                    'sample_id': client.sample_id,
                    'audio_path': client.metrics.audio_path,
                    'final_transcript': final_transcript,
                    'metrics': client.get_metrics_summary()
                }
                
                logger.debug(f"Finalized connection {client.conn_id}")
                
            except Exception as e:
                logger.error(f"Error finalizing connection {client.conn_id}: {e}")
                
        logger.info(f"Finalized {len(self.connection_results)} connections")
        
    def save_transcripts(self, transcripts_dir: Path) -> None:
        """Save final transcripts to files."""
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        
        for conn_id, results in self.connection_results.items():
            transcript_path = transcripts_dir / f"{conn_id}.txt"
            try:
                with open(transcript_path, 'w', encoding='utf-8') as f:
                    f.write(results.get('final_transcript', ''))
            except Exception as e:
                logger.error(f"Error saving transcript for {conn_id}: {e}")
                
        logger.info(f"Saved {len(self.connection_results)} transcripts to {transcripts_dir}")
        
    def generate_summary_report(self, 
                               quality_simple_json: Optional[Path],
                               judge_llm_json: Optional[Path]) -> str:
        """Generate markdown summary report."""
        
        # Get final metrics
        final_metrics = self.metrics_collector.metrics_history[-1] if self.metrics_collector.metrics_history else None
        
        # Calculate aggregate scores
        if final_metrics:
            J_bar = final_metrics.J
            mu_bar = final_metrics.mu
            sigma_bar = final_metrics.sigma
            p95_bar = final_metrics.p95_latency
            drops_bar = final_metrics.drops_percent
            gpu_bar = final_metrics.gpu_util
        else:
            J_bar = mu_bar = sigma_bar = p95_bar = drops_bar = gpu_bar = 0.0
            
        # Load quality metrics if available
        quality_metrics = {}
        if quality_simple_json and quality_simple_json.exists():
            try:
                with open(quality_simple_json, 'r', encoding='utf-8') as f:
                    quality_data = json.load(f)
                    quality_metrics = quality_data.get('aggregate_metrics', {})
            except Exception as e:
                logger.error(f"Error loading quality metrics: {e}")
                
        # Load judge metrics if available
        judge_metrics = {}
        if judge_llm_json and judge_llm_json.exists():
            try:
                with open(judge_llm_json, 'r', encoding='utf-8') as f:
                    judge_data = json.load(f)
                    judge_metrics = judge_data.get('aggregate_scores', {})
            except Exception as e:
                logger.error(f"Error loading judge metrics: {e}")
                
        # Generate summary
        summary = f"""# WhisperLive Optimization Test Results

## Configuration
- **WebSocket URL**: {self.config.ws_url}
- **Concurrency**: {self.config.concurrency}
- **Frame Rate**: {self.config.frame_ms}ms
- **Warmup**: {self.config.warmup_s}s
- **Run Duration**: {self.config.run_s}s
- **Language**: {self.config.language}
- **Model**: {self.config.model}

## Performance Metrics
- **Composite Score (J̄)**: {J_bar:.2f}
- **Throughput (μ̄)**: {mu_bar:.2f} transcript events/conn/10s
- **Fairness (σ̄)**: {sigma_bar:.2f}
- **Latency (p95)**: {p95_bar:.2f}s
- **Drop Rate**: {drops_bar:.1f}%
- **GPU Utilization**: {gpu_bar:.0f}%

## Steady State Detection
- **Reached**: {'Yes' if self.metrics_collector.is_steady_state() else 'No'}
- **Score**: {self.metrics_collector.get_steady_state_score():.2f}

## Quality Assessment

### Simple Text Metrics
"""
        
        if quality_metrics:
            summary += f"""- **Character Error Rate**: {quality_metrics.get('char_error_rate_mean', 0):.3f} ± {quality_metrics.get('char_error_rate_std', 0):.3f}
- **Word Error Rate**: {quality_metrics.get('word_error_rate_mean', 0):.3f} ± {quality_metrics.get('word_error_rate_std', 0):.3f}
- **Token F1**: {quality_metrics.get('token_f1_mean', 0):.3f} ± {quality_metrics.get('token_f1_std', 0):.3f}
- **Jaccard Similarity**: {quality_metrics.get('jaccard_similarity_mean', 0):.3f} ± {quality_metrics.get('jaccard_similarity_std', 0):.3f}
"""
        else:
            summary += "- No simple quality metrics available\n"
            
        summary += "\n### LLM Judge Assessment\n"
        
        if judge_metrics:
            summary += f"""- **Fidelity**: {judge_metrics.get('fidelity_mean', 0):.3f} ± {judge_metrics.get('fidelity_std', 0):.3f}
- **Omissions** (lower better): {judge_metrics.get('omissions_mean', 0):.3f} ± {judge_metrics.get('omissions_std', 0):.3f}
- **Insertions** (lower better): {judge_metrics.get('insertions_mean', 0):.3f} ± {judge_metrics.get('insertions_std', 0):.3f}
- **Overall**: {judge_metrics.get('overall_mean', 0):.3f} ± {judge_metrics.get('overall_std', 0):.3f}
- **Composite Score**: {judge_metrics.get('composite_mean', 0):.3f} ± {judge_metrics.get('composite_std', 0):.3f}
"""
        else:
            summary += "- No LLM judge assessment available\n"
            
        summary += f"""
## Test Details
- **Start Time**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.start_time)) if self.start_time else 'N/A'}
- **End Time**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.end_time)) if self.end_time else 'N/A'}
- **Duration**: {self.end_time - self.start_time:.1f}s (if both times available)
- **Connections**: {len(self.connection_results)}/{self.config.concurrency}
- **Audio Samples**: {len(self.audio_samples)}

## Files Generated
- `per_second.csv`: Per-second aggregated metrics
- `per_conn.csv`: Per-connection detailed metrics
- `transcripts/`: Final transcripts per connection
- `quality_simple.json`: Simple text quality metrics
- `judge_llm.json`: LLM judge assessment results
- `run.log.jsonl`: Real-time metrics log
"""
        
        return summary
        
    async def run_complete_test(self, 
                               enable_dashboard: bool = True,
                               enable_quality_simple: bool = True,
                               enable_quality_llm: bool = False) -> TestResults:
        """Run the complete optimization test."""
        
        self.start_time = time.time()
        logger.info("Starting WhisperLive optimization test")
        
        try:
            # Initialize clients
            if not await self.initialize_clients():
                raise RuntimeError("Failed to initialize clients")
                
            # Start audio streaming
            await self.start_audio_streaming()
            
            # Initialize metrics writer
            with MetricsWriter(self.output_dir) as metrics_writer:
                # Run test with dashboard
                dashboard = None
                if enable_dashboard:
                    total_seconds = self.config.warmup_s + self.config.run_s + self.config.cooldown_s
                    dashboard = LiveDashboard(
                        config={
                            'server': {
                                'compute_type': 'default',
                                'beam_size': 1,
                                'num_workers': 4,
                                'min_audio_s': 1.0
                            }
                        },
                        total_seconds=total_seconds,
                        ws_url=self.config.ws_url
                    )
                    
                    # Run dashboard loop
                    state_stream = self.generate_state_stream(metrics_writer)
                    final_state = dashboard.loop(state_stream)
                else:
                    # Run without dashboard
                    await self.run_test_phase("Warmup", self.config.warmup_s, metrics_writer=metrics_writer)
                    await self.run_test_phase("Main Run", self.config.run_s, metrics_writer=metrics_writer)
                    await self.run_test_phase("Cooldown", self.config.cooldown_s, metrics_writer=metrics_writer)
                    
                # Finalize connections
                await self.finalize_connections()
            
            self.end_time = time.time()
            
            # Create output paths
            transcripts_dir = self.output_dir / "transcripts"
            per_second_csv = self.output_dir / "per_second.csv"
            per_conn_csv = self.output_dir / "per_conn.csv"
            quality_simple_json = self.output_dir / "quality_simple.json" if enable_quality_simple else None
            judge_llm_json = self.output_dir / "judge_llm.json" if enable_quality_llm else None
            summary_md = self.output_dir / "summary.md"
            
            # Save transcripts
            self.save_transcripts(transcripts_dir)
            
            # Generate summary report
            summary_content = self.generate_summary_report(quality_simple_json, judge_llm_json)
            with open(summary_md, 'w', encoding='utf-8') as f:
                f.write(summary_content)
                
            # Create results object
            results = TestResults(
                config=self.config,
                start_time=self.start_time,
                end_time=self.end_time,
                duration=self.end_time - self.start_time,
                steady_state_reached=self.metrics_collector.is_steady_state(),
                steady_state_time=self.steady_state_start,
                steady_state_score=self.metrics_collector.get_steady_state_score(),
                final_metrics=self.metrics_collector.metrics_history[-1].__dict__ if self.metrics_collector.metrics_history else {},
                metrics_history=[m.__dict__ for m in self.metrics_collector.metrics_history],
                connection_results=self.connection_results,
                output_dir=self.output_dir,
                per_second_csv=per_second_csv,
                per_conn_csv=per_conn_csv,
                transcripts_dir=transcripts_dir,
                quality_simple_json=quality_simple_json,
                judge_llm_json=judge_llm_json,
                summary_md=summary_md
            )
            
            logger.info(f"Test completed successfully in {results.duration:.1f}s")
            logger.info(f"Results saved to {self.output_dir}")
            
            return results
            
        except Exception as e:
            logger.error(f"Test failed: {e}")
            raise
        finally:
            # Cleanup with timeout to prevent hanging
            for client in self.clients:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Client disconnect timeout: {client.conn_id}")
                except Exception as e:
                    logger.debug(f"Client disconnect error: {client.conn_id}: {e}")
