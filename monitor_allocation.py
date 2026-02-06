#!/usr/bin/env python3
"""
WhisperLive Server Load Monitoring Script

Monitors bot allocation across WhisperLive servers by:
1. Discovering servers from Consul
2. Querying each server's current session count
3. Displaying allocation in a matrix format (servers as rows, bots as columns)

Usage:
    python3 monitor_allocation.py [--interval SECONDS] [--consul-url URL]
"""

import requests
import json
import time
import argparse
import sys
from datetime import datetime
from typing import Dict, List, Tuple

class WhisperLiveMonitor:
    def __init__(self, consul_url: str = "http://localhost:8502"):
        self.consul_url = consul_url.rstrip('/')
        
    def discover_servers(self) -> List[Dict]:
        """Discover WhisperLive servers from Consul (only passing health) and dedupe by address:port"""
        try:
            # Use health API to get only passing services
            response = requests.get(f"{self.consul_url}/v1/health/service/whisperlive?passing=true", timeout=5)
            response.raise_for_status()
            entries = response.json()
            
            seen = set()
            servers: List[Dict] = []
            for entry in entries:
                service = entry.get('Service', {})
                address = service.get('Address')
                port = service.get('Port')
                service_id = service.get('ID')
                if not address or not port:
                    continue
                key = f"{address}:{port}"
                if key in seen:
                    continue
                seen.add(key)
                servers.append({
                    'id': service_id,
                    'address': address,
                    'port': port,
                    'metrics_url': f"http://{address}:9091/metrics"
                })
            return sorted(servers, key=lambda x: x['id'])
        except Exception as e:
            print(f"❌ Error discovering servers from Consul: {e}")
            return []
    
    def get_server_load(self, server: Dict) -> Tuple[int, int, str]:
        """Get current load from a WhisperLive server (no simulation)"""
        try:
            metrics_response = requests.get(server['metrics_url'], timeout=5)
            if metrics_response.status_code == 200:
                metrics_data = metrics_response.json()
                current_sessions = int(metrics_data.get('current_sessions', 0))
                max_clients = int(metrics_data.get('max_clients', 10))
                return current_sessions, max_clients, "healthy"
            return 0, 10, f"http_{metrics_response.status_code}"
        except Exception as e:
            return 0, 10, f"error: {str(e)[:20]}"
    
    def display_allocation_matrix(self, servers: List[Dict], loads: List[Tuple]):
        """Display server allocation in matrix format"""
        print("\n" + "="*80)
        print(f"📊 WhisperLive Server Load Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        if not servers:
            print("❌ No WhisperLive servers discovered")
            return
        
        # Header
        print(f"{'Server ID':<25} {'Address':<18} {'Load':<8} {'Status':<12} {'Load Bar':<20}")
        print("-" * 80)
        
        total_sessions = 0
        total_capacity = 0
        
        for i, server in enumerate(servers):
            current_sessions, max_clients, status = loads[i]
            total_sessions += current_sessions
            total_capacity += max_clients
            
            # Create load bar visualization
            load_pct = (current_sessions / max_clients) if max_clients > 0 else 0
            bar_length = 15
            filled_length = int(bar_length * load_pct)
            load_bar = "█" * filled_length + "░" * (bar_length - filled_length)
            
            # Color coding for status
            status_color = "🟢" if status == "healthy" else "🔴"
            
            print(f"{server['id']:<25} {server['address']}:{server['port']:<12} "
                  f"{current_sessions}/{max_clients:<6} {status_color}{status:<11} "
                  f"{load_bar} {load_pct:.1%}")
        
        print("-" * 80)
        print(f"📈 Total: {total_sessions}/{total_capacity} sessions "
              f"({(total_sessions/total_capacity)*100:.1f}% capacity)" if total_capacity > 0 else "")
        
        # Load balancing algorithm explanation
        print("\n💡 Load Balancing Algorithm:")
        print("   Traefik uses ROUND-ROBIN by default (not weighted/least-connections)")
        print("   • Each request goes to the next server in rotation")
        print("   • No consideration of current server load")
        print("   • For weighted load balancing, add server weights to Consul tags")
        print("   • For least-connections, would need custom Traefik middleware")
    
    def run_monitor(self, interval: int = 5):
        """Run continuous monitoring"""
        print("🚀 Starting WhisperLive Server Monitor...")
        print(f"📡 Consul URL: {self.consul_url}")
        print(f"⏱️  Update interval: {interval} seconds")
        print("Press Ctrl+C to stop\n")
        
        try:
            while True:
                servers = self.discover_servers()
                loads = [self.get_server_load(server) for server in servers]
                self.display_allocation_matrix(servers, loads)
                
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n\n👋 Monitoring stopped by user")
        except Exception as e:
            print(f"\n❌ Monitor error: {e}")

def main():
    parser = argparse.ArgumentParser(description="Monitor WhisperLive server allocation")
    parser.add_argument("--interval", "-i", type=int, default=1, 
                       help="Update interval in seconds (default: 1)")
    parser.add_argument("--consul-url", "-c", default="http://localhost:8502",
                       help="Consul HTTP URL (default: http://localhost:8502)")
    parser.add_argument("--once", action="store_true",
                       help="Run once and exit (no continuous monitoring)")
    
    args = parser.parse_args()
    
    monitor = WhisperLiveMonitor(consul_url=args.consul_url)
    
    if args.once:
        servers = monitor.discover_servers()
        loads = [monitor.get_server_load(server) for server in servers]
        monitor.display_allocation_matrix(servers, loads)
    else:
        monitor.run_monitor(interval=args.interval)

if __name__ == "__main__":
    main()
