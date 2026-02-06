#!/usr/bin/env python3
"""
Load Testing Script for Transcription Service

Tests:
1. Single worker max performance (direct connection)
2. Multi-worker scaling (via load balancer)

Measures:
- Requests per second (RPS)
- Response time (latency) - min, max, avg, p50, p95, p99
- Throughput (audio duration processed per second)
- Real-time factor (processing speed vs audio duration)
- Error rate
- Success rate
"""

import asyncio
import aiohttp
import time
import statistics
import json
import sys
import os
from pathlib import Path
from typing import List, Dict, Tuple
from dataclasses import dataclass
from collections import defaultdict
import argparse

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

@dataclass
class RequestResult:
    """Result of a single transcription request"""
    success: bool
    response_time: float
    audio_duration: float = 0.0
    error: str = ""
    status_code: int = 0

@dataclass
class LoadTestResults:
    """Aggregated results from a load test run"""
    total_requests: int
    successful_requests: int
    failed_requests: int
    response_times: List[float]
    audio_durations: List[float]
    errors: List[str]
    
    @property
    def success_rate(self) -> float:
        return (self.successful_requests / self.total_requests * 100) if self.total_requests > 0 else 0.0
    
    @property
    def error_rate(self) -> float:
        return (self.failed_requests / self.total_requests * 100) if self.total_requests > 0 else 0.0
    
    @property
    def avg_response_time(self) -> float:
        return statistics.mean(self.response_times) if self.response_times else 0.0
    
    @property
    def min_response_time(self) -> float:
        return min(self.response_times) if self.response_times else 0.0
    
    @property
    def max_response_time(self) -> float:
        return max(self.response_times) if self.response_times else 0.0
    
    @property
    def p50_response_time(self) -> float:
        return statistics.median(self.response_times) if self.response_times else 0.0
    
    @property
    def p95_response_time(self) -> float:
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        index = int(len(sorted_times) * 0.95)
        return sorted_times[index] if index < len(sorted_times) else sorted_times[-1]
    
    @property
    def p99_response_time(self) -> float:
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        index = int(len(sorted_times) * 0.99)
        return sorted_times[index] if index < len(sorted_times) else sorted_times[-1]
    
    @property
    def total_audio_duration(self) -> float:
        return sum(self.audio_durations)
    
    @property
    def total_test_duration(self) -> float:
        if not self.response_times:
            return 0.0
        # Approximate: max response time + some overhead
        return max(self.response_times) if self.response_times else 0.0
    
    @property
    def requests_per_second(self) -> float:
        if self.total_test_duration > 0:
            return self.total_requests / self.total_test_duration
        return 0.0
    
    @property
    def throughput_audio_per_second(self) -> float:
        """Audio duration processed per second of wall-clock time"""
        if self.total_test_duration > 0:
            return self.total_audio_duration / self.total_test_duration
        return 0.0
    
    @property
    def avg_real_time_factor(self) -> float:
        """Average real-time factor (audio_duration / response_time)"""
        if not self.response_times or not self.audio_durations:
            return 0.0
        rtfs = [dur / resp for dur, resp in zip(self.audio_durations, self.response_times) if resp > 0]
        return statistics.mean(rtfs) if rtfs else 0.0


async def send_transcription_request(
    session: aiohttp.ClientSession,
    url: str,
    audio_file_path: Path,
    timeout: float = 300.0
) -> RequestResult:
    """Send a single transcription request"""
    start_time = time.time()
    
    try:
        # Ensure URL ends with the correct endpoint
        if not url.endswith('/v1/audio/transcriptions'):
            if url.endswith('/'):
                url = url.rstrip('/') + '/v1/audio/transcriptions'
            else:
                url = url.rstrip('/') + '/v1/audio/transcriptions'
        
        with open(audio_file_path, 'rb') as f:
            form_data = aiohttp.FormData()
            form_data.add_field('file', f, filename=audio_file_path.name, content_type='audio/wav')
            form_data.add_field('model', 'large-v3-turbo')
            form_data.add_field('response_format', 'verbose_json')
            form_data.add_field('timestamp_granularities', 'segment')
            
            async with session.post(url, data=form_data, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                response_time = time.time() - start_time
                
                if response.status == 200:
                    data = await response.json()
                    audio_duration = data.get('duration', 0.0)
                    return RequestResult(
                        success=True,
                        response_time=response_time,
                        audio_duration=audio_duration,
                        status_code=200
                    )
                else:
                    error_text = await response.text()
                    return RequestResult(
                        success=False,
                        response_time=response_time,
                        error=f"HTTP {response.status}: {error_text[:200]}",
                        status_code=response.status
                    )
    except asyncio.TimeoutError:
        return RequestResult(
            success=False,
            response_time=time.time() - start_time,
            error="Request timeout"
        )
    except Exception as e:
        return RequestResult(
            success=False,
            response_time=time.time() - start_time,
            error=f"Exception: {str(e)[:200]}"
        )


async def run_load_test(
    url: str,
    audio_file_path: Path,
    num_requests: int,
    concurrency: int,
    timeout: float = 300.0
) -> LoadTestResults:
    """Run a load test with specified parameters"""
    print(f"  Running {num_requests} requests with concurrency={concurrency}...")
    
    connector = aiohttp.TCPConnector(limit=concurrency * 2)
    async with aiohttp.ClientSession(connector=connector) as session:
        semaphore = asyncio.Semaphore(concurrency)
        
        async def bounded_request():
            async with semaphore:
                return await send_transcription_request(session, url, audio_file_path, timeout)
        
        start_time = time.time()
        tasks = [bounded_request() for _ in range(num_requests)]
        results = await asyncio.gather(*tasks)
        total_duration = time.time() - start_time
    
    # Aggregate results
    response_times = []
    audio_durations = []
    errors = []
    successful = 0
    failed = 0
    
    for result in results:
        response_times.append(result.response_time)
        if result.success:
            successful += 1
            audio_durations.append(result.audio_duration)
        else:
            failed += 1
            errors.append(result.error)
    
    test_results = LoadTestResults(
        total_requests=num_requests,
        successful_requests=successful,
        failed_requests=failed,
        response_times=response_times,
        audio_durations=audio_durations,
        errors=errors
    )
    
    # Override total_test_duration with actual measured duration
    test_results._total_test_duration = total_duration
    
    return test_results


def print_results(results: LoadTestResults, test_name: str):
    """Print formatted test results"""
    print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}  {test_name}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}\n")
    
    print(f"{Colors.BOLD}Requests:{Colors.ENDC}")
    print(f"  Total:        {results.total_requests}")
    print(f"  Successful:   {Colors.OKGREEN}{results.successful_requests}{Colors.ENDC}")
    print(f"  Failed:       {Colors.FAIL if results.failed_requests > 0 else Colors.OKGREEN}{results.failed_requests}{Colors.ENDC}")
    print(f"  Success Rate: {Colors.OKGREEN if results.success_rate >= 95 else Colors.WARNING}{results.success_rate:.2f}%{Colors.ENDC}")
    print(f"  Error Rate:   {Colors.FAIL if results.error_rate > 5 else Colors.OKGREEN}{results.error_rate:.2f}%{Colors.ENDC}")
    
    print(f"\n{Colors.BOLD}Response Time (seconds):{Colors.ENDC}")
    print(f"  Min:  {results.min_response_time:.3f}s")
    print(f"  Max:  {results.max_response_time:.3f}s")
    print(f"  Avg:  {results.avg_response_time:.3f}s")
    print(f"  P50:  {results.p50_response_time:.3f}s")
    print(f"  P95:  {results.p95_response_time:.3f}s")
    print(f"  P99:  {results.p99_response_time:.3f}s")
    
    print(f"\n{Colors.BOLD}Performance:{Colors.ENDC}")
    print(f"  Requests/sec:        {results.requests_per_second:.2f}")
    print(f"  Throughput:          {results.throughput_audio_per_second:.2f}x audio duration/sec")
    print(f"  Avg Real-time Factor: {results.avg_real_time_factor:.2f}x")
    print(f"  Total Test Duration: {results.total_test_duration:.2f}s")
    
    if results.errors:
        print(f"\n{Colors.BOLD}Errors (first 5):{Colors.ENDC}")
        for error in results.errors[:5]:
            print(f"  {Colors.FAIL}{error}{Colors.ENDC}")


def find_max_performance(
    url: str,
    audio_file_path: Path,
    min_requests: int = 10,
    max_requests: int = 100,
    timeout: float = 300.0
) -> Tuple[LoadTestResults, int]:
    """Find maximum performance by testing different concurrency levels"""
    print(f"\n{Colors.BOLD}{Colors.OKCYAN}Finding Maximum Performance (Single Worker){Colors.ENDC}")
    print(f"{Colors.OKCYAN}{'='*70}{Colors.ENDC}\n")
    
    best_rps = 0.0
    best_results = None
    best_concurrency = 1
    
    # Test different concurrency levels
    concurrency_levels = [1, 2, 4, 8, 16, 32, 64]
    
    for concurrency in concurrency_levels:
        if concurrency > max_requests:
            continue
        
        num_requests = min(max_requests, max(min_requests, concurrency * 2))
        
        print(f"\n{Colors.BOLD}Testing concurrency={concurrency}, requests={num_requests}{Colors.ENDC}")
        
        try:
            results = asyncio.run(run_load_test(url, audio_file_path, num_requests, concurrency, timeout))
            print_results(results, f"Concurrency {concurrency}")
            
            if results.requests_per_second > best_rps and results.success_rate >= 95:
                best_rps = results.requests_per_second
                best_results = results
                best_concurrency = concurrency
            
            # Stop if error rate is too high
            if results.error_rate > 10:
                print(f"\n{Colors.WARNING}Error rate too high ({results.error_rate:.2f}%), stopping search{Colors.ENDC}")
                break
                
        except Exception as e:
            print(f"{Colors.FAIL}Error during test: {e}{Colors.ENDC}")
            break
    
    return best_results, best_concurrency


async def test_scaling(
    lb_url: str,
    audio_file_path: Path,
    num_workers: int,
    requests_per_worker: int = 20,
    concurrency: int = 4,
    timeout: float = 300.0
) -> LoadTestResults:
    """Test performance with specified number of workers"""
    print(f"\n{Colors.BOLD}{Colors.OKCYAN}Testing with {num_workers} worker(s){Colors.ENDC}")
    print(f"{Colors.OKCYAN}{'='*70}{Colors.ENDC}\n")
    
    total_requests = num_workers * requests_per_worker
    print(f"  Total requests: {total_requests} ({requests_per_worker} per worker)")
    
    results = await run_load_test(lb_url, audio_file_path, total_requests, concurrency, timeout)
    print_results(results, f"Load Balancer with {num_workers} Worker(s)")
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Load test transcription service')
    parser.add_argument('--api-url', default='http://localhost:8083', help='API URL (default: http://localhost:8083)')
    parser.add_argument('--worker-url', default='http://localhost:8000', help='Direct worker URL for single worker test (default: http://localhost:8000)')
    parser.add_argument('--audio-file', default='tests/test_audio.wav', help='Path to test audio file')
    parser.add_argument('--test-single', action='store_true', help='Test single worker max performance')
    parser.add_argument('--test-scaling', action='store_true', help='Test multi-worker scaling')
    parser.add_argument('--test-all', action='store_true', help='Run all tests (default)')
    parser.add_argument('--workers', type=int, nargs='+', default=[1, 2, 3], help='Number of workers to test (default: 1 2 3)')
    parser.add_argument('--requests', type=int, default=20, help='Requests per worker for scaling test (default: 20)')
    parser.add_argument('--concurrency', type=int, default=4, help='Concurrency for scaling test (default: 4)')
    parser.add_argument('--timeout', type=float, default=300.0, help='Request timeout in seconds (default: 300)')
    
    args = parser.parse_args()
    
    audio_file_path = Path(args.audio_file)
    if not audio_file_path.exists():
        print(f"{Colors.FAIL}Error: Audio file not found: {audio_file_path}{Colors.ENDC}")
        sys.exit(1)
    
    # Default to test-all if no specific test is selected
    if not (args.test_single or args.test_scaling):
        args.test_all = True
    
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}  Transcription Service Load Test{Colors.ENDC}")
    print(f"{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}\n")
    print(f"API URL (Load Balancer): {args.api_url}")
    print(f"Worker URL (Direct):      {args.worker_url}")
    print(f"Audio File:               {audio_file_path}")
    print(f"Audio Size:               {audio_file_path.stat().st_size / 1024:.1f} KB\n")
    
    all_results = {}
    
    # Test 1: Single worker max performance
    if args.test_all or args.test_single:
        # Try to get worker IP for direct access
        import subprocess
        try:
            worker_ip = subprocess.check_output(
                ['docker', 'inspect', '-f', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', 'transcription-worker-1'],
                stderr=subprocess.DEVNULL
            ).decode().strip()
            if worker_ip:
                worker_direct_url = f"http://{worker_ip}:8000"
                print(f"{Colors.OKGREEN}Found worker 1 at: {worker_direct_url}{Colors.ENDC}\n")
            else:
                worker_direct_url = args.worker_url
                print(f"{Colors.WARNING}Using provided worker URL: {worker_direct_url}{Colors.ENDC}\n")
        except:
            worker_direct_url = args.worker_url
            print(f"{Colors.WARNING}Using provided worker URL: {worker_direct_url}{Colors.ENDC}")
            print(f"{Colors.WARNING}Note: For accurate single-worker test, ensure only 1 worker is running{Colors.ENDC}\n")
        
        try:
            best_results, best_concurrency = find_max_performance(
                worker_direct_url,
                audio_file_path,
                timeout=args.timeout
            )
            if best_results:
                all_results['single_worker_max'] = {
                    'results': best_results,
                    'optimal_concurrency': best_concurrency
                }
                print(f"\n{Colors.BOLD}{Colors.OKGREEN}Optimal Concurrency: {best_concurrency}{Colors.ENDC}")
                print(f"{Colors.BOLD}{Colors.OKGREEN}Max RPS: {best_results.requests_per_second:.2f}{Colors.ENDC}")
        except Exception as e:
            print(f"{Colors.FAIL}Single worker test failed: {e}{Colors.ENDC}")
            print(f"{Colors.WARNING}Continuing with scaling tests...{Colors.ENDC}\n")
    
    # Test 2: Multi-worker scaling
    if args.test_all or args.test_scaling:
        scaling_results = {}
        
        # Convert workers to list if it's a single int
        workers_list = args.workers if isinstance(args.workers, list) else [args.workers]
        
        for num_workers in workers_list:
            try:
                results = asyncio.run(test_scaling(
                    args.api_url,
                    audio_file_path,
                    num_workers,
                    args.requests,
                    args.concurrency,
                    args.timeout
                ))
                scaling_results[num_workers] = results
                all_results[f'{num_workers}_workers'] = results
                
                # Wait a bit between tests
                if num_workers != workers_list[-1]:
                    print(f"\n{Colors.OKCYAN}Waiting 5 seconds before next test...{Colors.ENDC}\n")
                    time.sleep(5)
            except Exception as e:
                print(f"{Colors.FAIL}Scaling test for {num_workers} workers failed: {e}{Colors.ENDC}")
        
        # Print scaling summary
        if scaling_results:
            print(f"\n{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}")
            print(f"{Colors.BOLD}{Colors.HEADER}  Scaling Summary{Colors.ENDC}")
            print(f"{Colors.BOLD}{Colors.HEADER}{'='*70}{Colors.ENDC}\n")
            
            print(f"{'Workers':<10} {'RPS':<15} {'Avg Latency':<15} {'RT Factor':<15} {'Success Rate':<15}")
            print("-" * 70)
            
            for num_workers in sorted(scaling_results.keys()):
                r = scaling_results[num_workers]
                print(f"{num_workers:<10} {r.requests_per_second:<15.2f} {r.avg_response_time:<15.3f} "
                      f"{r.avg_real_time_factor:<15.2f} {r.success_rate:<15.2f}%")
            
            # Calculate scaling efficiency
            if len(scaling_results) > 1:
                print(f"\n{Colors.BOLD}Scaling Efficiency:{Colors.ENDC}")
                base_workers = min(scaling_results.keys())
                base_rps = scaling_results[base_workers].requests_per_second
                
                for num_workers in sorted(scaling_results.keys()):
                    if num_workers == base_workers:
                        continue
                    rps = scaling_results[num_workers].requests_per_second
                    expected_rps = base_rps * num_workers
                    efficiency = (rps / expected_rps * 100) if expected_rps > 0 else 0
                    print(f"  {num_workers} workers: {rps:.2f} RPS (expected: {expected_rps:.2f}, efficiency: {efficiency:.1f}%)")
    
    print(f"\n{Colors.BOLD}{Colors.OKGREEN}Load testing complete!{Colors.ENDC}\n")


if __name__ == '__main__':
    main()









