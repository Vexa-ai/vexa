#!/bin/bash

# Redis WhisperLive Monitoring Script
# Monitors wl:rank (server rankings) and wl:hb:* (heartbeats) in real-time

# Configuration
REDIS_CONTAINER="${REDIS_CONTAINER:-vexa_dev-redis-1}"
REFRESH_INTERVAL="${REFRESH_INTERVAL:-2}"
SHOW_HEARTBEATS="${SHOW_HEARTBEATS:-true}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to get current timestamp
get_timestamp() {
    date '+%H:%M:%S'
}

# Function to check if Redis container is running
check_redis() {
    if ! docker ps --format "table {{.Names}}" | grep -q "^${REDIS_CONTAINER}$"; then
        echo -e "${RED}ERROR: Redis container '${REDIS_CONTAINER}' not found or not running${NC}"
        echo "Available containers:"
        docker ps --format "table {{.Names}}\t{{.Status}}" | grep redis
        exit 1
    fi
}

# Function to execute Redis command
redis_cmd() {
    docker exec "${REDIS_CONTAINER}" redis-cli "$@" 2>/dev/null
}

# Function to get server rankings
get_server_rankings() {
    echo -e "${CYAN}=== WhisperLive Server Rankings (wl:rank) ===${NC}"
    local rankings=$(redis_cmd ZRANGE wl:rank 0 -1 WITHSCORES)
    
    if [ -z "$rankings" ]; then
        echo -e "${YELLOW}No servers registered${NC}"
        return
    fi
    
    # Use awk to parse the rankings properly
    echo "$rankings" | awk 'NR%2==1{url=$0} NR%2==0{
        sessions=$0
        if (sessions == 0) color="\033[0;34m"
        else if (sessions > 2) color="\033[1;33m"
        else color="\033[0;32m"
        printf "  %s%s\033[0m → %s sessions\n", color, url, sessions
    }'
}

# Function to get heartbeats
get_heartbeats() {
    if [ "$SHOW_HEARTBEATS" != "true" ]; then
        return
    fi
    
    echo -e "\n${CYAN}=== Server Heartbeats (wl:hb:*) ===${NC}"
    local heartbeat_keys=$(redis_cmd KEYS "wl:hb:*")
    
    if [ -z "$heartbeat_keys" ]; then
        echo -e "${YELLOW}No heartbeats found${NC}"
        return
    fi
    
    for key in $heartbeat_keys; do
        if [ -n "$key" ]; then
            local ttl=$(redis_cmd TTL "$key")
            local server_url=${key#wl:hb:}
            
            if [ "$ttl" = "-1" ]; then
                echo -e "  ${GREEN}$server_url${NC} → ∞ (no expiration)"
            elif [ "$ttl" = "-2" ]; then
                echo -e "  ${RED}$server_url${NC} → EXPIRED"
            elif [ "$ttl" -lt 5 ] 2>/dev/null; then
                echo -e "  ${RED}$server_url${NC} → TTL: ${ttl}s"
            elif [ "$ttl" -lt 10 ] 2>/dev/null; then
                echo -e "  ${YELLOW}$server_url${NC} → TTL: ${ttl}s"
            else
                echo -e "  ${GREEN}$server_url${NC} → TTL: ${ttl}s"
            fi
        fi
    done
}

# Function to get additional stats
get_stats() {
    echo -e "\n${CYAN}=== Statistics ===${NC}"
    local total_servers=$(redis_cmd ZCARD wl:rank)
    local total_sessions=$(redis_cmd EVAL "local sum=0; local scores=redis.call('ZRANGE','wl:rank',0,-1,'WITHSCORES'); for i=2,#scores,2 do sum=sum+tonumber(scores[i]) end; return sum" 0)
    
    # Clean up the output (remove (integer) prefix if present)
    total_servers=$(echo "$total_servers" | sed 's/(integer) //')
    total_sessions=$(echo "$total_sessions" | sed 's/(integer) //')
    
    echo -e "  Total servers: ${GREEN}${total_servers:-0}${NC}"
    echo -e "  Total sessions: ${GREEN}${total_sessions:-0}${NC}"
    
    if [ "${total_servers:-0}" -gt 0 ] 2>/dev/null && [ "${total_sessions:-0}" -gt 0 ] 2>/dev/null; then
        if command -v bc &> /dev/null; then
            local avg_sessions=$(echo "scale=2; ${total_sessions:-0} / ${total_servers:-1}" | bc -l 2>/dev/null)
            echo -e "  Average sessions/server: ${GREEN}${avg_sessions}${NC}"
        fi
    fi
}

# Function to show help
show_help() {
    echo "Redis WhisperLive Monitor"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -c, --container NAME    Redis container name (default: vexa_dev-redis-1)"
    echo "  -i, --interval SECONDS  Refresh interval (default: 2)"
    echo "  -n, --no-heartbeats     Don't show heartbeat information"
    echo "  -h, --help              Show this help"
    echo ""
    echo "Environment variables:"
    echo "  REDIS_CONTAINER         Same as --container"
    echo "  REFRESH_INTERVAL        Same as --interval"
    echo "  SHOW_HEARTBEATS         Set to 'false' to disable heartbeats"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Default monitoring"
    echo "  $0 -c vexa-redis-1 -i 1             # Custom container, 1s refresh"
    echo "  $0 --no-heartbeats                   # Rankings only"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--container)
            REDIS_CONTAINER="$2"
            shift 2
            ;;
        -i|--interval)
            REFRESH_INTERVAL="$2"
            shift 2
            ;;
        -n|--no-heartbeats)
            SHOW_HEARTBEATS="false"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Variables to cache previous output for smooth display
CACHED_RANKINGS=""
CACHED_HEARTBEATS=""
CACHED_STATS=""

# Main monitoring loop
main() {
    check_redis
    
    echo -e "${GREEN}Redis WhisperLive Monitor${NC}"
    echo -e "Container: ${CYAN}${REDIS_CONTAINER}${NC}"
    echo -e "Refresh interval: ${CYAN}${REFRESH_INTERVAL}s${NC}"
    echo -e "Press ${YELLOW}Ctrl+C${NC} to exit"
    echo ""
    
    # Get initial data
    CACHED_RANKINGS=$(get_server_rankings 2>/dev/null)
    if [ "$SHOW_HEARTBEATS" = "true" ]; then
        CACHED_HEARTBEATS=$(get_heartbeats 2>/dev/null)
    fi
    CACHED_STATS=$(get_stats 2>/dev/null)
    
    # Display initial screen
    clear
    local current_time=$(get_timestamp)
    echo -e "${GREEN}Redis WhisperLive Monitor${NC} - $current_time"
    echo -e "Container: ${CYAN}${REDIS_CONTAINER}${NC} | Refresh: ${CYAN}${REFRESH_INTERVAL}s${NC}"
    echo ""
    echo "$CACHED_RANKINGS"
    if [ "$SHOW_HEARTBEATS" = "true" ]; then
        echo "$CACHED_HEARTBEATS"
    fi
    echo "$CACHED_STATS"
    echo -e "\n${YELLOW}Press Ctrl+C to exit${NC}"

    while true; do
        sleep "$REFRESH_INTERVAL"
        
        # Fetch ALL new data in background while old display remains
        local new_rankings=""
        local new_heartbeats=""
        local new_stats=""
        local fetch_time=$(get_timestamp)
        
        # Fetch all data at once (this happens while old screen is still visible)
        new_rankings=$(get_server_rankings 2>/dev/null)
        if [ "$SHOW_HEARTBEATS" = "true" ]; then
            new_heartbeats=$(get_heartbeats 2>/dev/null)
        fi
        new_stats=$(get_stats 2>/dev/null)
        
        # Update cache only if we got new data
        if [ -n "$new_rankings" ]; then
            CACHED_RANKINGS="$new_rankings"
        fi
        if [ -n "$new_heartbeats" ]; then
            CACHED_HEARTBEATS="$new_heartbeats"
        fi
        if [ -n "$new_stats" ]; then
            CACHED_STATS="$new_stats"
        fi
        
        # NOW update the display all at once with fresh data
        clear
        echo -e "${GREEN}Redis WhisperLive Monitor${NC} - $fetch_time"
        echo -e "Container: ${CYAN}${REDIS_CONTAINER}${NC} | Refresh: ${CYAN}${REFRESH_INTERVAL}s${NC}"
        echo ""
        echo "$CACHED_RANKINGS"
        if [ "$SHOW_HEARTBEATS" = "true" ]; then
            echo "$CACHED_HEARTBEATS"
        fi
        echo "$CACHED_STATS"
        echo -e "\n${YELLOW}Press Ctrl+C to exit${NC}"
    done
}

# Handle Ctrl+C gracefully
trap 'echo -e "\n${GREEN}Monitoring stopped${NC}"; exit 0' INT

# Check if bc is available for calculations
if ! command -v bc &> /dev/null; then
    echo -e "${YELLOW}Warning: 'bc' not found. Some calculations may not work.${NC}"
fi

# Run the monitor
main 