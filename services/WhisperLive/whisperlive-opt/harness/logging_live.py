"""
Live Dashboard for WhisperLive Optimization Testing

Provides real-time console display with rich formatting, sparklines,
and live metrics updates during test execution.
"""

import time
from collections import deque
from typing import Dict, List, Any, Optional, Iterator
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.text import Text
from rich.layout import Layout
from rich.align import Align
from rich import box

console = Console()


def format_sparkline(values: List[float], width: int = 30, height: int = 7) -> str:
    """Create ASCII sparkline from numeric values."""
    if not values:
        return "▁" * width
        
    # Normalize values to height range
    min_val = min(values)
    max_val = max(values)
    
    if max_val == min_val:
        return "▁" * min(len(values), width)
        
    # Sparkline blocks (7 levels)
    blocks = "▁▂▃▄▅▆▇"
    
    # Take last width values
    recent_values = values[-width:] if len(values) > width else values
    
    sparkline = ""
    for val in recent_values:
        # Normalize to 0-6 range
        normalized = (val - min_val) / (max_val - min_val)
        block_idx = int(normalized * (len(blocks) - 1))
        block_idx = max(0, min(block_idx, len(blocks) - 1))
        sparkline += blocks[block_idx]
        
    return sparkline


def format_duration(seconds: float) -> str:
    """Format duration in MM:SS format."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


class LiveDashboard:
    """Live console dashboard for WhisperLive optimization testing."""
    
    def __init__(self, 
                 config: Dict[str, Any],
                 total_seconds: int,
                 ws_url: str = "ws://localhost:9090/ws"):
        
        self.config = config
        self.total_seconds = total_seconds
        self.ws_url = ws_url
        
        # History for sparklines
        self.mu_history = deque(maxlen=60)  # Last 60 seconds
        self.latency_history = deque(maxlen=60)
        self.gpu_history = deque(maxlen=60)
        
        # Progress tracking
        self.progress = Progress(
            TextColumn("[bold blue]Run Progress"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console
        )
        self.task = self.progress.add_task("Optimization Test", total=total_seconds)
        
        # Notices history
        self.notices = deque(maxlen=10)
        
    def add_notice(self, message: str, level: str = "info"):
        """Add a notice message."""
        timestamp = time.strftime("%H:%M:%S")
        self.notices.append({
            'timestamp': timestamp,
            'message': message,
            'level': level
        })
        
    def render_header(self) -> Table:
        """Render the header with configuration info."""
        header = Table(show_header=False, padding=(0, 1))
        
        # Server configuration
        server_config = []
        if 'compute_type' in self.config.get('server', {}):
            server_config.append(f"compute={self.config['server']['compute_type']}")
        if 'beam_size' in self.config.get('server', {}):
            server_config.append(f"beam={self.config['server']['beam_size']}")
        if 'num_workers' in self.config.get('server', {}):
            server_config.append(f"workers={self.config['server']['num_workers']}")
        if 'min_audio_s' in self.config.get('server', {}):
            server_config.append(f"min_audio={self.config['server']['min_audio_s']}")
            
        config_str = ", ".join(server_config) if server_config else "default"
        
        header.add_row(
            f"[bold cyan]WhisperLive Isolated Test[/bold cyan]  |  "
            f"[yellow]cfg: {config_str}[/yellow]"
        )
        
        # Connection info
        run_config = self.config.get('run', {})
        concurrency = run_config.get('concurrency', 32)
        frame_ms = run_config.get('frame_ms', 20)
        warmup_s = run_config.get('warmup_s', 20)
        run_s = run_config.get('run_s', 120)
        
        header.add_row(
            f"[blue]WS: {self.ws_url}[/blue]  |  "
            f"[green]Concurrency: {concurrency}[/green]  |  "
            f"[magenta]Frame: {frame_ms}ms[/magenta]  |  "
            f"[yellow]Warmup: {warmup_s}s[/yellow]  "
            f"[yellow]Run: {run_s}s[/yellow]"
        )
        
        # Data info
        data_config = self.config.get('data', {})
        manifest = data_config.get('manifest', 'data/manifest.csv')
        language = self.config.get('server', {}).get('language', 'en')
        
        header.add_row(
            f"[cyan]Audio: {manifest}[/cyan]  |  "
            f"[green]Language: {language}[/green]"
        )
        
        return header
        
    def render_metrics(self, state: Dict[str, Any]) -> Table:
        """Render the main metrics table."""
        metrics_table = Table(show_header=False, padding=(0, 1))
        
        # Throughput section
        mu = state.get('mu', 0.0)
        sigma = state.get('sigma', 0.0)
        J = state.get('J', 0.0)
        lam = state.get('lambda', 0.5)
        penalties = state.get('penalties', 0.0)
        
        # Color code based on performance
        mu_color = "green" if mu > 5.0 else "yellow" if mu > 2.0 else "red"
        J_color = "green" if J > 5.0 else "yellow" if J > 2.0 else "red"
        
        throughput_line = (
            f"[bold]THROUGHPUT (10s):[/bold]   "
            f"μ=[{mu_color}]{mu:.2f}[/{mu_color}]   "
            f"σ=[white]{sigma:.2f}[/white]   "
            f"J(λ={lam})=[{J_color}]{J:.2f}[/{J_color}]   "
            f"penalties=[red]{penalties:.2f}[/red]"
        )
        metrics_table.add_row(throughput_line)
        
        # Latency section
        p50 = state.get('p50', 0.0)
        p95 = state.get('p95', 0.0)
        drops = state.get('drops', 0.0)
        
        # Color code latency based on SLO
        latency_slo = self.config.get('metrics', {}).get('latency_slo', 2.0)
        p50_color = "green" if p50 < latency_slo else "yellow" if p50 < latency_slo * 1.5 else "red"
        p95_color = "green" if p95 < latency_slo else "yellow" if p95 < latency_slo * 1.5 else "red"
        drops_color = "green" if drops < 1.0 else "yellow" if drops < 5.0 else "red"
        
        latency_line = (
            f"[bold]LATENCY:[/bold]\t\t"
            f"p50=[{p50_color}]{p50:.2f}s[/{p50_color}]   "
            f"p95=[{p95_color}]{p95:.2f}s[/{p95_color}]   "
            f"drops=[{drops_color}]{drops:.1f}%[/{drops_color}]"
        )
        metrics_table.add_row(latency_line)
        
        # GPU section
        gpu_util = state.get('gpu_util', 0.0)
        vram_mb = state.get('vram_mb', 0.0)
        vram_gb = vram_mb / 1024.0 if vram_mb > 0 else 0.0
        
        gpu_color = "green" if gpu_util < 90 else "yellow" if gpu_util < 95 else "red"
        
        gpu_line = (
            f"[bold]GPU:[/bold]\t\t\t"
            f"util=[{gpu_color}]{gpu_util:.0f}%[/{gpu_color}]   "
            f"vram=[cyan]{vram_gb:.1f}GB[/cyan]"
        )
        metrics_table.add_row(gpu_line)
        
        return metrics_table
        
    def render_top_connections(self, top_connections: List[Dict[str, Any]]) -> Table:
        """Render top 5 slow/quiet connections."""
        if not top_connections:
            return Table(show_header=True, header_style="bold magenta")
            
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("#", justify="right", style="cyan")
        table.add_column("C10s", justify="right", style="white")
        table.add_column("rate/s", justify="right", style="green")
        table.add_column("p95", justify="right", style="yellow")
        table.add_column("drops", justify="right", style="red")
        table.add_column("meeting", style="blue", overflow="fold")
        
        for conn in top_connections[:5]:
            # Color code based on performance
            c10s_color = "red" if conn['c10s'] < 2 else "yellow" if conn['c10s'] < 5 else "green"
            rate_color = "red" if conn['rate'] < 0.2 else "yellow" if conn['rate'] < 0.5 else "green"
            
            table.add_row(
                conn['id'],
                f"[{c10s_color}]{conn['c10s']}[/{c10s_color}]",
                f"[{rate_color}]{conn['rate']:.2f}[/{rate_color}]",
                f"{conn['p95']:.2f}s",
                f"{conn['drops']:.1f}%",
                conn['label'][:30] + "..." if len(conn['label']) > 30 else conn['label']
            )
            
        return table
        
    def render_sparklines(self) -> Table:
        """Render sparklines for key metrics."""
        spark_table = Table(show_header=False, padding=(0, 1))
        
        if self.mu_history:
            mu_spark = format_sparkline(list(self.mu_history), 30)
            spark_table.add_row(f"μ10s: {mu_spark}")
            
        if self.latency_history:
            lat_spark = format_sparkline(list(self.latency_history), 30)
            spark_table.add_row(f"p95 : {lat_spark}")
            
        if self.gpu_history:
            gpu_spark = format_sparkline(list(self.gpu_history), 30)
            spark_table.add_row(f"GPU : {gpu_spark}")
            
        return spark_table
        
    def render_notices(self) -> Optional[Panel]:
        """Render recent notices."""
        if not self.notices:
            return None
            
        notices_text = []
        for notice in list(self.notices)[-5:]:  # Last 5 notices
            level_color = {
                'info': 'blue',
                'warning': 'yellow',
                'error': 'red',
                'success': 'green'
            }.get(notice['level'], 'white')
            
            notices_text.append(
                f"[{level_color}]{notice['timestamp']}[/{level_color}] {notice['message']}"
            )
            
        return Panel(
            "\n".join(notices_text),
            title="[bold]Notices[/bold]",
            border_style="dim"
        )
        
    def render_status_line(self, t_rel: float, state: Dict[str, Any]) -> str:
        """Render the status line."""
        active = state.get('active', 0)
        N = state.get('N', 0)
        
        # Check for steady state
        steady_state = state.get('steady_state', False)
        steady_marker = " [green]✓ STEADY[/green]" if steady_state else ""
        
        return (
            f"[ t={format_duration(t_rel)} / {format_duration(self.total_seconds)} ]  "
            f"active={active}/{N}{steady_marker}"
        )
        
    def render(self, t_rel: float, state: Dict[str, Any]) -> Panel:
        """Render the complete dashboard."""
        # Update progress - disabled to avoid slice error
        # self.progress.update(self.task, completed=min(t_rel, self.total_seconds))
        
        # Update history for sparklines
        self.mu_history.append(state.get('mu', 0.0))
        self.latency_history.append(state.get('p95', 0.0))
        self.gpu_history.append(state.get('gpu_util', 0.0))
        
        # Create a simple panel instead of complex layout
        status_text = f"{self.render_status_line(t_rel, state)}\n\n"
        # Remove the progress object display to avoid the slice error
        
        # Add basic metrics
        mu = state.get('mu', 0.0)
        sigma = state.get('sigma', 0.0)
        p95 = state.get('p95', 0.0)
        status_text += f"Throughput (μ): {mu:.2f} req/s\n"
        status_text += f"Latency (σ): {sigma:.3f}s\n"
        status_text += f"P95 Latency: {p95:.3f}s\n"
        
        return Panel(
            Text(status_text),
            title="[bold]WhisperLive Optimization Test[/bold]",
            border_style="bright_blue"
        )
        
    def render_final_summary(self, final_metrics: Dict[str, Any]) -> Panel:
        """Render final summary."""
        J_bar = final_metrics.get('J_bar', 0.0)
        mu_bar = final_metrics.get('mu_bar', 0.0)
        sigma_bar = final_metrics.get('sigma_bar', 0.0)
        p95_bar = final_metrics.get('p95_bar', 0.0)
        drops_bar = final_metrics.get('drops_bar', 0.0)
        gpu_bar = final_metrics.get('gpu_bar', 0.0)
        
        summary_text = (
            f"[bold green]DONE[/bold green]  |  "
            f"J̄=[bold]{J_bar:.2f}[/bold]   "
            f"μ̄={mu_bar:.2f}   "
            f"σ̄={sigma_bar:.2f}   "
            f"p95={p95_bar:.2f}s   "
            f"drops={drops_bar:.1f}%   "
            f"gpu={gpu_bar:.0f}%"
        )
        
        return Panel(
            summary_text,
            title="[bold]Final Results[/bold]",
            border_style="green"
        )
        
    def loop(self, state_iter: Iterator[Dict[str, Any]]) -> Dict[str, Any]:
        """Main dashboard loop."""
        final_metrics = {}
        
        with Live(refresh_per_second=4, console=console) as live:
            try:
                for state in state_iter:
                    t_rel = state.get('t_rel', 0.0)
                    
                    # Check for completion
                    if t_rel >= self.total_seconds:
                        break
                        
                    # Update dashboard
                    layout = self.render(t_rel, state)
                    live.update(layout)
                    
                    # Store final metrics
                    if t_rel >= self.total_seconds - 1:
                        final_metrics = state
                        
            except KeyboardInterrupt:
                self.add_notice("Test interrupted by user", "warning")
                
        # Show final summary
        if final_metrics:
            summary = self.render_final_summary(final_metrics)
            console.print(summary)
            
        return final_metrics
