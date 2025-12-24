#!/bin/bash
# Helper script to run load tests with different worker configurations

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Load Test Runner${NC}"
echo -e "${BLUE}========================================${NC}\n"

# Check if services are running
if ! docker ps | grep -q transcription-lb; then
    echo -e "${RED}Error: Transcription services are not running${NC}"
    echo -e "${YELLOW}Please start services with: docker-compose up -d${NC}"
    exit 1
fi

# Check if test audio exists
TEST_AUDIO="$SCRIPT_DIR/test_audio.wav"
if [ ! -f "$TEST_AUDIO" ]; then
    echo -e "${RED}Error: Test audio file not found: $TEST_AUDIO${NC}"
    exit 1
fi

# Function to get worker port (for direct access)
get_worker_port() {
    local worker_id=$1
    # Check if port is exposed (docker-compose might not expose it)
    # For now, we'll use the load balancer and assume worker 1 is accessible
    echo "8000"
}

# Function to test single worker (direct access)
test_single_worker() {
    echo -e "\n${YELLOW}=== Testing Single Worker (Direct Access) ===${NC}\n"
    
    # Scale to 1 worker
    echo -e "${BLUE}Scaling to 1 worker for single-worker test...${NC}"
    "$SCRIPT_DIR/scale_workers.sh" 1
    sleep 3
    
    # Get worker 1 container IP (if accessible)
    WORKER_IP=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' transcription-worker-1 2>/dev/null || echo "")
    
    if [ -n "$WORKER_IP" ]; then
        WORKER_URL="http://$WORKER_IP:8000"
        echo -e "${GREEN}Found worker 1 at: $WORKER_URL${NC}\n"
    else
        echo -e "${YELLOW}Worker direct access not available. Using load balancer.${NC}\n"
        WORKER_URL="http://localhost:8083"
    fi
    
    python3 "$SCRIPT_DIR/load_test.py" \
        --test-single \
        --worker-url "$WORKER_URL" \
        --audio-file "$TEST_AUDIO" \
        --timeout 300
}

# Function to test scaling
test_scaling() {
    local workers="${1:-1 2 3}"
    local requests_per_worker="${2:-20}"
    local concurrency="${3:-4}"
    
    echo -e "\n${YELLOW}=== Testing Multi-Worker Scaling ===${NC}\n"
    echo -e "Workers to test: ${workers}"
    echo -e "Requests per worker: ${requests_per_worker}"
    echo -e "Concurrency: ${concurrency}\n"
    
    # Test each worker count sequentially
    for num_workers in $workers; do
        echo -e "${BLUE}Scaling to $num_workers worker(s)...${NC}"
        "$SCRIPT_DIR/scale_workers.sh" "$num_workers"
        sleep 5
        
        # Run test for this specific worker count
        python3 "$SCRIPT_DIR/load_test.py" \
            --test-scaling \
            --api-url "http://localhost:8083" \
            --audio-file "$TEST_AUDIO" \
            --workers "$num_workers" \
            --requests "$requests_per_worker" \
            --concurrency "$concurrency" \
            --timeout 300
        
        # Wait between tests (except after the last one)
        last_worker=$(echo $workers | awk '{print $NF}')
        if [ "$num_workers" != "$last_worker" ]; then
            echo -e "\n${BLUE}Waiting 5 seconds before next test...${NC}\n"
            sleep 5
        fi
    done
}

# Function to run all tests
test_all() {
    echo -e "\n${YELLOW}=== Running All Load Tests ===${NC}\n"
    
    # Test 1: Single worker max performance
    test_single_worker
    
    # Wait between tests
    echo -e "\n${BLUE}Waiting 10 seconds before scaling tests...${NC}\n"
    sleep 10
    
    # Test 2: Multi-worker scaling
    test_scaling "1 2 3" 20 4
}

# Parse arguments
case "${1:-all}" in
    single)
        test_single_worker
        ;;
    scaling)
        test_scaling "${2:-1 2 3}" "${3:-20}" "${4:-4}"
        ;;
    all)
        test_all
        ;;
    *)
        echo "Usage: $0 [single|scaling|all] [workers] [requests_per_worker] [concurrency]"
        echo ""
        echo "Examples:"
        echo "  $0 all                          # Run all tests"
        echo "  $0 single                       # Test single worker max performance"
        echo "  $0 scaling                      # Test scaling with 1,2,3 workers"
        echo "  $0 scaling '1 2 3' 30 8        # Test scaling with custom params"
        exit 1
        ;;
esac

echo -e "\n${GREEN}Load testing complete!${NC}\n"









