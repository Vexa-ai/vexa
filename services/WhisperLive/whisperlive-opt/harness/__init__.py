"""
WhisperLive Optimization Harness

A comprehensive testing and optimization framework for WhisperLive server performance.
Provides deterministic testing with static audio samples, real-time metrics collection,
quality assessment, and parameter sweep capabilities.
"""

from .ws_client import WhisperLiveClient, create_client_pool, connect_clients
from .metrics import MetricsCollector, MetricsWriter, AggregatedMetrics
from .collector import TestCollector, TestConfig, TestResults
from .compare_simple import QualityComparator, QualityMetrics
from .compare_llm import LLMJudge, JudgeScore
from .logging_live import LiveDashboard
from .runner import WhisperLiveOptimizer

__version__ = "1.0.0"
__author__ = "Vexa AI"

__all__ = [
    "WhisperLiveClient",
    "create_client_pool", 
    "connect_clients",
    "MetricsCollector",
    "MetricsWriter",
    "AggregatedMetrics",
    "TestCollector",
    "TestConfig",
    "TestResults",
    "QualityComparator",
    "QualityMetrics",
    "LLMJudge",
    "JudgeScore",
    "LiveDashboard",
    "WhisperLiveOptimizer"
]
