"""
Metrics Collection and Analysis System

Implements sliding window counters, latency tracking, penalty calculations,
and CSV/JSONL output for WhisperLive optimization testing.
"""

import csv
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class GPUMetrics:
    """GPU utilization and memory metrics."""
    util_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    temperature_c: float = 0.0
    power_w: float = 0.0
    timestamp: float = 0.0


@dataclass
class AggregatedMetrics:
    """Aggregated metrics for a time window."""
    timestamp: float
    t_rel: float
    N: int
    active_connections: int
    
    # Throughput metrics
    mu: float = 0.0  # Mean transcript events per connection (10s window)
    sigma: float = 0.0  # Std dev of transcript events
    J: float = 0.0  # Composite score
    
    # Latency metrics
    p50_latency: float = 0.0
    p95_latency: float = 0.0
    avg_latency: float = 0.0
    
    # Drop metrics
    total_drops: int = 0
    drops_percent: float = 0.0
    
    # WebSocket metrics
    ws_sent_total: int = 0
    ws_recv_total: int = 0
    
    # GPU metrics
    gpu_util: float = 0.0
    vram_mb: float = 0.0
    
    # Penalties
    penalties: float = 0.0
    penalty_reasons: List[str] = field(default_factory=list)
    
    # Top connections (slowest/quietest)
    top_connections: List[Dict[str, Any]] = field(default_factory=list)


class SlidingWindowCounter:
    """Sliding window counter for time-series metrics."""
    
    def __init__(self, window_seconds: float = 10.0, max_samples: int = 1000):
        self.window_seconds = window_seconds
        self.max_samples = max_samples
        self.events = deque(maxlen=max_samples)
        
    def add_event(self, timestamp: float, value: float = 1.0):
        """Add an event with timestamp and value."""
        self.events.append((timestamp, value))
        
    def get_count(self, current_time: Optional[float] = None) -> int:
        """Get count of events in the sliding window."""
        if current_time is None:
            current_time = time.time()
            
        cutoff = current_time - self.window_seconds
        
        # Remove old events
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()
            
        return len(self.events)
    
    def get_sum(self, current_time: Optional[float] = None) -> float:
        """Get sum of values in the sliding window."""
        if current_time is None:
            current_time = time.time()
            
        cutoff = current_time - self.window_seconds
        
        # Remove old events and sum values
        total = 0.0
        while self.events and self.events[0][0] < cutoff:
            total += self.events.popleft()[1]
            
        # Add remaining events
        for timestamp, value in self.events:
            if timestamp >= cutoff:
                total += value
                
        return total
    
    def get_mean(self, current_time: Optional[float] = None) -> float:
        """Get mean value in the sliding window."""
        count = self.get_count(current_time)
        if count == 0:
            return 0.0
        return self.get_sum(current_time) / count


class MetricsCollector:
    """Collects and aggregates metrics from multiple connections."""
    
    def __init__(self, 
                 lambda_penalty: float = 0.5,
                 latency_slo: float = 2.0,
                 drop_slo: float = 0.02,
                 gpu_sample_interval: float = 1.0):
        
        self.lambda_penalty = lambda_penalty
        self.latency_slo = latency_slo
        self.drop_slo = drop_slo
        self.gpu_sample_interval = gpu_sample_interval
        
        # Connection tracking
        self.connections: Dict[str, Any] = {}
        
        # Metrics history
        self.metrics_history: List[AggregatedMetrics] = []
        
        # GPU monitoring
        self.last_gpu_sample = 0.0
        self.gpu_metrics = GPUMetrics()
        
        # Steady state detection
        self.ewma_history = deque(maxlen=20)  # 20-second EWMA history
        self.steady_state_start = None
        
    def add_connection(self, conn_id: str, client_metrics: Dict[str, Any]):
        """Add or update connection metrics."""
        self.connections[conn_id] = client_metrics
        
    def remove_connection(self, conn_id: str):
        """Remove connection from tracking."""
        self.connections.pop(conn_id, None)
        
    def sample_gpu_metrics(self) -> GPUMetrics:
        """Sample GPU metrics if enabled."""
        current_time = time.time()
        
        if (self.gpu_sample_interval > 0 and 
            current_time - self.last_gpu_sample >= self.gpu_sample_interval):
            
            try:
                import pynvml
                pynvml.nvmlInit()
                
                # Get GPU 0 metrics
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                
                # Utilization
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                self.gpu_metrics.util_percent = util.gpu
                
                # Memory
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                self.gpu_metrics.memory_used_mb = mem_info.used / (1024 * 1024)
                self.gpu_metrics.memory_total_mb = mem_info.total / (1024 * 1024)
                
                # Temperature
                try:
                    temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                    self.gpu_metrics.temperature_c = temp
                except:
                    pass
                    
                # Power
                try:
                    power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0  # Convert to watts
                    self.gpu_metrics.power_w = power
                except:
                    pass
                    
                self.gpu_metrics.timestamp = current_time
                self.last_gpu_sample = current_time
                
            except ImportError:
                logger.debug("pynvml not available, GPU metrics disabled")
            except Exception as e:
                logger.debug(f"GPU sampling error: {e}")
                
        return self.gpu_metrics
        
    def calculate_penalties(self, metrics: AggregatedMetrics) -> Tuple[float, List[str]]:
        """Calculate penalty score and reasons."""
        penalties = 0.0
        reasons = []
        
        # Latency SLO penalty
        if metrics.p95_latency > self.latency_slo:
            penalty = 0.5
            penalties += penalty
            reasons.append(f"p95_latency_{metrics.p95_latency:.1f}s>{self.latency_slo}s")
            
        # Drop rate penalty
        if metrics.drops_percent > (self.drop_slo * 100):
            penalty = 0.5
            penalties += penalty
            reasons.append(f"drop_rate_{metrics.drops_percent:.1f}%>{self.drop_slo*100:.1f}%")
            
        # GPU utilization penalty
        if metrics.gpu_util > 95.0:
            penalty = 0.25
            penalties += penalty
            reasons.append(f"gpu_util_{metrics.gpu_util:.1f}%>95%")
            
        # Connection drops penalty
        if metrics.active_connections < metrics.N:
            penalty = 5.0
            penalties += penalty
            reasons.append(f"connection_loss_{metrics.N-metrics.active_connections}")
            
        return penalties, reasons
        
    def aggregate_metrics(self, current_time: float, t_rel: float) -> AggregatedMetrics:
        """Aggregate metrics from all connections."""
        
        # Sample GPU metrics
        gpu_metrics = self.sample_gpu_metrics()
        
        if not self.connections:
            return AggregatedMetrics(
                timestamp=current_time,
                t_rel=t_rel,
                N=0,
                active_connections=0,
                gpu_util=gpu_metrics.util_percent,
                vram_mb=gpu_metrics.memory_used_mb
            )
        
        # Collect per-connection metrics
        c10s_values = []
        latencies = []
        total_drops = 0
        total_sent = 0
        total_recv = 0
        active_connections = 0
        
        connection_details = []
        
        for conn_id, client_metrics in self.connections.items():
            if not client_metrics.get('is_connected', False):
                continue
                
            active_connections += 1
            
            # Get 10-second window count
            c10s = client_metrics.get('c10s', 0)
            c10s_values.append(c10s)
            
            # Latency metrics
            avg_lat = client_metrics.get('avg_latency', 0)
            p95_lat = client_metrics.get('p95_latency', 0)
            if avg_lat > 0:
                latencies.append(avg_lat)
            if p95_lat > 0:
                latencies.append(p95_lat)
                
            # Drop metrics
            frames_dropped = client_metrics.get('frames_dropped', 0)
            frames_sent = client_metrics.get('frames_sent', 0)
            total_drops += frames_dropped
            total_sent += frames_sent
            total_recv += client_metrics.get('transcript_events', 0)
            
            # Store connection details for top connections
            connection_details.append({
                'id': conn_id,
                'c10s': c10s,
                'rate': client_metrics.get('rate_per_s', 0),
                'p95': p95_lat,
                'drops': client_metrics.get('drop_rate', 0),
                'label': client_metrics.get('meeting_label', 'unknown')
            })
        
        # Calculate aggregated metrics
        mu = np.mean(c10s_values) if c10s_values else 0.0
        sigma = np.std(c10s_values) if c10s_values else 0.0
        
        # Composite score
        J = mu - self.lambda_penalty * sigma
        
        # Latency percentiles
        if latencies:
            p50_latency = np.percentile(latencies, 50)
            p95_latency = np.percentile(latencies, 95)
            avg_latency = np.mean(latencies)
        else:
            p50_latency = p95_latency = avg_latency = 0.0
            
        # Drop percentage
        drops_percent = (total_drops / max(total_sent, 1)) * 100.0
        
        # Create aggregated metrics
        metrics = AggregatedMetrics(
            timestamp=current_time,
            t_rel=t_rel,
            N=len(self.connections),
            active_connections=active_connections,
            mu=mu,
            sigma=sigma,
            J=J,
            p50_latency=p50_latency,
            p95_latency=p95_latency,
            avg_latency=avg_latency,
            total_drops=total_drops,
            drops_percent=drops_percent,
            ws_sent_total=total_sent,
            ws_recv_total=total_recv,
            gpu_util=gpu_metrics.util_percent,
            vram_mb=gpu_metrics.memory_used_mb
        )
        
        # Calculate penalties
        penalties, penalty_reasons = self.calculate_penalties(metrics)
        metrics.penalties = penalties
        metrics.penalty_reasons = penalty_reasons
        
        # Apply penalties to composite score
        metrics.J -= penalties
        
        # Sort connections by performance (slowest/quietest first)
        connection_details.sort(key=lambda x: (x['c10s'], x['p95']))
        metrics.top_connections = connection_details[:5]
        
        # Store in history
        self.metrics_history.append(metrics)
        
        # Update steady state detection
        self._update_steady_state(metrics)
        
        return metrics
        
    def _update_steady_state(self, metrics: AggregatedMetrics):
        """Update EWMA for steady state detection."""
        self.ewma_history.append(metrics.J)
        
        if len(self.ewma_history) < 10:
            return
            
        # Calculate EWMA
        alpha = 0.1  # Smoothing factor
        ewma = list(self.ewma_history)[0]
        for value in list(self.ewma_history)[1:]:
            ewma = alpha * value + (1 - alpha) * ewma
            
        # Check for steady state (change < 3% for 20 seconds)
        if len(self.ewma_history) >= 20:
            recent_ewma = np.mean(list(self.ewma_history)[-10:])
            if abs(recent_ewma - ewma) / max(ewma, 1.0) < 0.03:
                if self.steady_state_start is None:
                    self.steady_state_start = time.time()
            else:
                self.steady_state_start = None
                
    def is_steady_state(self) -> bool:
        """Check if system is in steady state."""
        return (self.steady_state_start is not None and 
                time.time() - self.steady_state_start >= 20.0)
                
    def get_steady_state_score(self) -> float:
        """Get mean J score over steady state period."""
        if not self.is_steady_state():
            return 0.0
            
        # Get metrics from last 30 seconds of steady state
        current_time = time.time()
        cutoff = current_time - 30.0
        
        steady_metrics = [
            m for m in self.metrics_history 
            if m.timestamp >= cutoff
        ]
        
        if not steady_metrics:
            return 0.0
            
        return np.mean([m.J for m in steady_metrics])


class MetricsWriter:
    """Writes metrics to CSV and JSONL files."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # CSV writers
        self.per_second_csv = None
        self.per_conn_csv = None
        
        # JSONL writer
        self.jsonl_file = None
        
    def __enter__(self):
        """Context manager entry."""
        # Per-second CSV
        per_second_path = self.output_dir / "per_second.csv"
        self.per_second_csv = open(per_second_path, 'w', newline='', encoding='utf-8')
        per_second_writer = csv.writer(self.per_second_csv)
        
        # Write header
        per_second_writer.writerow([
            'ts', 't_rel', 'N', 'active', 'mu', 'sigma', 'J', 'lambda',
            'p50_latency', 'p95_latency', 'avg_latency', 'drops_total', 'drops_percent',
            'ws_sent_total', 'ws_recv_total', 'gpu_util', 'vram_mb', 'penalties'
        ])
        self.per_second_csv.flush()
        
        # Per-connection CSV
        per_conn_path = self.output_dir / "per_conn.csv"
        self.per_conn_csv = open(per_conn_path, 'w', newline='', encoding='utf-8')
        per_conn_writer = csv.writer(self.per_conn_csv)
        
        # Write header
        per_conn_writer.writerow([
            'ts', 'conn_id', 'meeting_label', 'sample_id', 'C10s', 'rate_per_s',
            'sends', 'recvs', 'drops', 'avg_latency', 'p95_latency', 'drops_percent'
        ])
        self.per_conn_csv.flush()
        
        # JSONL file
        jsonl_path = self.output_dir / "run.log.jsonl"
        self.jsonl_file = open(jsonl_path, 'w', encoding='utf-8')
        
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self.per_second_csv:
            self.per_second_csv.close()
        if self.per_conn_csv:
            self.per_conn_csv.close()
        if self.jsonl_file:
            self.jsonl_file.close()
            
    def write_per_second_metrics(self, metrics: AggregatedMetrics, lambda_penalty: float):
        """Write per-second aggregated metrics."""
        if not self.per_second_csv:
            return
            
        writer = csv.writer(self.per_second_csv)
        writer.writerow([
            metrics.timestamp,
            metrics.t_rel,
            metrics.N,
            metrics.active_connections,
            metrics.mu,
            metrics.sigma,
            metrics.J,
            lambda_penalty,
            metrics.p50_latency,
            metrics.p95_latency,
            metrics.avg_latency,
            metrics.total_drops,
            metrics.drops_percent,
            metrics.ws_sent_total,
            metrics.ws_recv_total,
            metrics.gpu_util,
            metrics.vram_mb,
            metrics.penalties
        ])
        self.per_second_csv.flush()
        
    def write_per_connection_metrics(self, connections: Dict[str, Any], timestamp: float):
        """Write per-connection metrics."""
        if not self.per_conn_csv:
            return
            
        writer = csv.writer(self.per_conn_csv)
        
        for conn_id, client_metrics in connections.items():
            writer.writerow([
                timestamp,
                conn_id,
                client_metrics.get('meeting_label', ''),
                client_metrics.get('sample_id', ''),
                client_metrics.get('c10s', 0),
                client_metrics.get('rate_per_s', 0),
                client_metrics.get('frames_sent', 0),
                client_metrics.get('transcript_events', 0),
                client_metrics.get('frames_dropped', 0),
                client_metrics.get('avg_latency', 0),
                client_metrics.get('p95_latency', 0),
                client_metrics.get('drop_rate', 0)
            ])
            
        self.per_conn_csv.flush()
        
    def write_jsonl_metrics(self, metrics: AggregatedMetrics):
        """Write metrics as JSONL entry."""
        if not self.jsonl_file:
            return
            
        data = {
            'ts': metrics.timestamp,
            't_rel': metrics.t_rel,
            'mu': metrics.mu,
            'sigma': metrics.sigma,
            'J': metrics.J,
            'p50': metrics.p50_latency,
            'p95': metrics.p95_latency,
            'drops': metrics.drops_percent / 100.0,
            'gpu_util': metrics.gpu_util,
            'vram_mb': metrics.vram_mb,
            'penalties': metrics.penalties,
            'penalty_reasons': metrics.penalty_reasons
        }
        
        self.jsonl_file.write(json.dumps(data) + '\n')
        self.jsonl_file.flush()
