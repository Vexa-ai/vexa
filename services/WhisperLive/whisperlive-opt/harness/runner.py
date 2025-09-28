"""
Main Runner for WhisperLive Optimization Testing

Orchestrates the complete optimization testing workflow including:
- Single test runs
- Parameter sweeps
- Quality assessment
- Results aggregation and reporting
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import yaml
import pandas as pd
import subprocess

from .collector import TestCollector, TestConfig, TestResults
from .compare_simple import compare_quality_simple
from .compare_llm import compare_quality_llm

logger = logging.getLogger(__name__)


class WhisperLiveOptimizer:
    """Main optimizer class for WhisperLive testing."""
    
    def __init__(self, config_path: str, output_dir: str):
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        # Load configuration
        self.config = self._load_config()
        
        # Initialize results storage
        self.results: List[TestResults] = []
        
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                
            logger.info(f"Loaded configuration from {self.config_path}")
            return config
            
        except Exception as e:
            logger.error(f"Error loading config {self.config_path}: {e}")
            raise
            
    def _create_test_config(self, config_override: Optional[Dict[str, Any]] = None) -> TestConfig:
        """Create TestConfig from loaded configuration."""
        
        # Start with base config
        config = self.config.copy()
        
        # Apply overrides if provided
        if config_override:
            config.update(config_override)
            
        # Create TestConfig object
        return TestConfig(
            ws_url=config['server']['ws_url'],
            language=config['server'].get('language', 'en'),
            model=config['server'].get('model', 'small'),
            auth_header=config['server'].get('auth_header'),
            concurrency=config['run']['concurrency'],
            frame_ms=config['run']['frame_ms'],
            warmup_s=config['run']['warmup_s'],
            run_s=config['run']['run_s'],
            cooldown_s=config['run'].get('cooldown_s', 10),
            repeat_audio=config['run'].get('repeat_audio', True),
            shuffle_audio=config['run'].get('shuffle_audio', True),
            per_conn_seed=config['run'].get('per_conn_seed', True),
            lambda_penalty=config['metrics']['lambda'],
            latency_slo=config['metrics']['latency_slo'],
            drop_slo=config['metrics']['drop_slo'],
            gpu_sample_interval=config['metrics']['gpu_sample_s'],
            manifest_path=config['data']['manifest']
        )
        
    def _setup_server_environment(self, server_params: Dict[str, Any]) -> None:
        """Set up environment variables for WhisperLive server."""
        
        # Common server environment variables
        env_vars = {
            'WL_LOG_LEVEL': 'INFO',
            'WL_LOG_TRANSCRIPTS': 'false',
            'WL_LOG_TRANSCRIPT_SUMMARY': 'true'
        }
        
        # Add server parameters as environment variables
        for key, value in server_params.items():
            if key in ['compute_type', 'beam_size', 'num_workers', 'min_audio_s', 
                      'vad_onset', 'vad_no_speech_thresh']:
                env_vars[f'WL_{key.upper()}'] = str(value)
                
        # Set environment variables
        for key, value in env_vars.items():
            os.environ[key] = value
            
        logger.info(f"Set server environment: {env_vars}")
        
    async def run_single_test(self, 
                             test_config: TestConfig,
                             run_id: str,
                             enable_quality_simple: bool = True,
                             enable_quality_llm: bool = False) -> TestResults:
        """Run a single optimization test."""
        
        # Create output directory for this run
        run_output_dir = self.output_dir / run_id
        run_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create test collector
        collector = TestCollector(test_config, run_output_dir)
        
        # Run the test
        results = await collector.run_complete_test(
            enable_dashboard=False,
            enable_quality_simple=enable_quality_simple,
            enable_quality_llm=enable_quality_llm
        )
        
        # Post-process results
        await self._post_process_results(results, test_config)
        
        return results
        
    async def _post_process_results(self, 
                                  results: TestResults,
                                  test_config: TestConfig) -> None:
        """Post-process test results with quality assessment."""
        
        try:
            # Prepare transcript data for quality assessment
            transcripts = {}
            manifest = {}
            
            for conn_id, conn_results in results.connection_results.items():
                transcripts[conn_id] = {
                    'transcript': conn_results.get('final_transcript', ''),
                    'sample_id': conn_results.get('sample_id', '')
                }
                
            # Load manifest for golden references
            import pandas as pd
            manifest_df = pd.read_csv(test_config.manifest_path)
            for _, row in manifest_df.iterrows():
                manifest[row['sample_id']] = {
                    'sample_id': row['sample_id'],
                    'golden_path': row['golden_path']
                }
                
            # Run simple quality comparison
            if results.quality_simple_json:
                try:
                    compare_quality_simple(
                        transcripts=transcripts,
                        manifest=manifest,
                        output_path=results.quality_simple_json
                    )
                    logger.info(f"Completed simple quality assessment: {results.quality_simple_json}")
                except Exception as e:
                    logger.error(f"Simple quality assessment failed: {e}")
                    
            # Run LLM judge comparison
            if results.judge_llm_json:
                try:
                    llm_config = self.config.get('quality', {})
                    compare_quality_llm(
                        transcripts=transcripts,
                        manifest=manifest,
                        output_path=results.judge_llm_json,
                        provider=llm_config.get('llm_provider', 'openai'),
                        model=llm_config.get('llm_model', 'gpt-4o-mini'),
                        api_key=os.getenv(f"{llm_config.get('llm_provider', 'openai').upper()}_API_KEY")
                    )
                    logger.info(f"Completed LLM judge assessment: {results.judge_llm_json}")
                except Exception as e:
                    logger.error(f"LLM judge assessment failed: {e}")
                    
        except Exception as e:
            logger.error(f"Post-processing failed: {e}")
            
    async def run_parameter_sweep(self) -> List[TestResults]:
        """Run parameter sweep testing."""
        
        if 'sweep' not in self.config:
            raise ValueError("No sweep configuration found in config file")
            
        sweep_config = self.config['sweep']
        parameter = sweep_config['parameter']
        values = sweep_config['values']
        execution_config = self.config.get('execution', {})
        
        logger.info(f"Starting parameter sweep: {parameter} = {values}")
        
        results = []
        
        for i, value in enumerate(values):
            run_id = f"sweep_{parameter}_{value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            logger.info(f"Running sweep iteration {i+1}/{len(values)}: {parameter}={value}")
            
            # Create test config with parameter override
            param_override = {parameter: value}
            test_config = self._create_test_config(param_override)
            
            # Set up server environment if needed
            if execution_config.get('restart_server_between_runs', False):
                self._setup_server_environment({parameter: value})
                
            try:
                # Run test
                result = await self.run_single_test(
                    test_config=test_config,
                    run_id=run_id,
                    enable_quality_simple=self.config['quality'].get('enable_simple', True),
                    enable_quality_llm=self.config['quality'].get('enable_llm', False)
                )
                
                results.append(result)
                
                # Add parameter value to results
                result.sweep_parameter = parameter
                result.sweep_value = value
                
                logger.info(f"Completed sweep iteration {i+1}/{len(values)}")
                
                # Delay between runs if configured
                if execution_config.get('server_restart_delay', 0) > 0 and i < len(values) - 1:
                    delay = execution_config['server_restart_delay']
                    logger.info(f"Waiting {delay}s before next iteration...")
                    await asyncio.sleep(delay)
                    
            except Exception as e:
                logger.error(f"Sweep iteration {i+1} failed: {e}")
                continue
                
        # Generate sweep summary
        if results and execution_config.get('aggregate_results', True):
            await self._generate_sweep_summary(results, sweep_config)
            
        return results
        
    async def _generate_sweep_summary(self, 
                                    results: List[TestResults],
                                    sweep_config: Dict[str, Any]) -> None:
        """Generate summary report for parameter sweep."""
        
        try:
            # Create summary data
            summary_data = []
            
            for result in results:
                # Extract key metrics
                final_metrics = result.final_metrics
                
                summary_entry = {
                    'parameter': result.sweep_parameter,
                    'value': result.sweep_value,
                    'composite_score': final_metrics.get('J', 0.0),
                    'throughput': final_metrics.get('mu', 0.0),
                    'fairness': final_metrics.get('sigma', 0.0),
                    'latency_p95': final_metrics.get('p95_latency', 0.0),
                    'drops_percent': final_metrics.get('drops_percent', 0.0),
                    'gpu_util': final_metrics.get('gpu_util', 0.0),
                    'steady_state_score': result.steady_state_score,
                    'duration': result.duration
                }
                
                # Add quality metrics if available
                if result.quality_simple_json and result.quality_simple_json.exists():
                    try:
                        with open(result.quality_simple_json, 'r') as f:
                            quality_data = json.load(f)
                            agg_metrics = quality_data.get('aggregate_metrics', {})
                            summary_entry['char_error_rate'] = agg_metrics.get('char_error_rate_mean', 0.0)
                            summary_entry['token_f1'] = agg_metrics.get('token_f1_mean', 0.0)
                    except Exception as e:
                        logger.debug(f"Error loading quality metrics: {e}")
                        
                summary_data.append(summary_entry)
                
            # Create DataFrame and sort by primary metric
            df = pd.DataFrame(summary_data)
            primary_metric = sweep_config.get('rank_by_metric', 'composite_score')
            
            if primary_metric in df.columns:
                df = df.sort_values(primary_metric, ascending=False)
                
            # Save summary
            summary_path = self.output_dir / f"sweep_summary_{sweep_config['parameter']}.csv"
            df.to_csv(summary_path, index=False)
            
            # Generate markdown report
            markdown_path = self.output_dir / f"sweep_summary_{sweep_config['parameter']}.md"
            with open(markdown_path, 'w') as f:
                f.write(f"# Parameter Sweep Results: {sweep_config['parameter']}\n\n")
                f.write(f"**Description**: {sweep_config.get('description', '')}\n\n")
                f.write(f"**Parameter Values**: {sweep_config['values']}\n\n")
                f.write(f"**Primary Metric**: {primary_metric}\n\n")
                f.write("## Results Summary\n\n")
                f.write(df.to_markdown(index=False))
                f.write("\n\n## Best Configuration\n\n")
                
                if not df.empty:
                    best = df.iloc[0]
                    f.write(f"- **{sweep_config['parameter']}**: {best['value']}\n")
                    f.write(f"- **{primary_metric}**: {best[primary_metric]:.3f}\n")
                    f.write(f"- **Throughput**: {best['throughput']:.2f}\n")
                    f.write(f"- **Latency (p95)**: {best['latency_p95']:.2f}s\n")
                    
            logger.info(f"Generated sweep summary: {markdown_path}")
            
        except Exception as e:
            logger.error(f"Error generating sweep summary: {e}")
            
    async def run(self) -> List[TestResults]:
        """Run the optimization test(s)."""
        
        start_time = time.time()
        
        try:
            if 'sweep' in self.config:
                # Run parameter sweep
                results = await self.run_parameter_sweep()
            else:
                # Run single test
                test_config = self._create_test_config()
                run_id = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                
                result = await self.run_single_test(
                    test_config=test_config,
                    run_id=run_id,
                    enable_quality_simple=self.config['quality'].get('enable_simple', True),
                    enable_quality_llm=self.config['quality'].get('enable_llm', False)
                )
                
                results = [result]
                
            total_time = time.time() - start_time
            logger.info(f"Optimization testing completed in {total_time:.1f}s")
            logger.info(f"Results saved to {self.output_dir}")
            
            return results
            
        except Exception as e:
            logger.error(f"Optimization testing failed: {e}")
            raise


def main():
    """Main entry point for the optimizer."""
    
    parser = argparse.ArgumentParser(
        description="WhisperLive Optimization Testing Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run baseline test
  python -m harness.runner --config configs/baseline.yaml --out results/

  # Run greedy decoding test
  python -m harness.runner --config configs/greedy.yaml --out results/

  # Run parameter sweep
  python -m harness.runner --config configs/sweep_num_workers.yaml --out results/

  # Run with LLM judge enabled
  python -m harness.runner --config configs/beam_search.yaml --out results/ --llm-judge

  # Run with custom output directory
  python -m harness.runner --config configs/baseline.yaml --out results/$(date +%Y%m%d_%H%M%S)/
        """
    )
    
    parser.add_argument(
        '--config', '-c',
        required=True,
        help='Path to YAML configuration file'
    )
    
    parser.add_argument(
        '--out', '-o',
        default='results/',
        help='Output directory for results (default: results/)'
    )
    
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level (default: INFO)'
    )
    
    parser.add_argument(
        '--llm-judge',
        action='store_true',
        help='Enable LLM judge assessment (overrides config)'
    )
    
    parser.add_argument(
        '--no-simple-quality',
        action='store_true',
        help='Disable simple quality metrics (overrides config)'
    )
    
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show configuration and exit without running'
    )
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    try:
        # Create optimizer
        optimizer = WhisperLiveOptimizer(args.config, args.out)
        
        # Override quality settings if specified
        if args.llm_judge:
            optimizer.config['quality']['enable_llm'] = True
            
        if args.no_simple_quality:
            optimizer.config['quality']['enable_simple'] = False
            
        # Dry run mode
        if args.dry_run:
            print("Configuration:")
            print(yaml.dump(optimizer.config, default_flow_style=False))
            return 0
            
        # Run optimization
        results = asyncio.run(optimizer.run())
        
        # Print summary
        if results:
            print(f"\n✓ Completed {len(results)} test(s)")
            print(f"Results saved to: {args.out}")
            
            # Print best result if sweep
            if len(results) > 1:
                best_result = max(results, key=lambda r: r.steady_state_score)
                print(f"Best configuration: {getattr(best_result, 'sweep_value', 'N/A')}")
                print(f"Best score: {best_result.steady_state_score:.3f}")
                
        return 0
        
    except KeyboardInterrupt:
        print("\n⚠️ Test interrupted by user")
        return 1
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
