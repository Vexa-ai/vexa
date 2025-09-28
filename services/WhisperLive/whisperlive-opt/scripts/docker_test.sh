#!/bin/bash
# Docker-based WhisperLive Optimization Testing Script

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
CONFIG="configs/docker.yaml"
OUTPUT="results/docker_$(date +%Y%m%d_%H%M%S)"
BUILD_IMAGES=false
START_SERVER=false
RUN_TEST=false
CLEANUP=false

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Docker-based WhisperLive Optimization Testing"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -c, --config CONFIG     Configuration file (default: configs/docker.yaml)"
    echo "  -o, --output OUTPUT     Output directory (default: results/docker_TIMESTAMP)"
    echo "  -b, --build            Build Docker images"
    echo "  -s, --start-server     Start WhisperLive server"
    echo "  -t, --test             Run optimization test"
    echo "  -a, --all              Build, start server, and run test"
    echo "  --cleanup              Clean up containers and volumes"
    echo "  -h, --help             Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 --all                                    # Complete workflow"
    echo "  $0 --build --start-server                   # Build and start server"
    echo "  $0 --test -c configs/baseline.yaml          # Run test with specific config"
    echo "  $0 --cleanup                                # Clean up everything"
}

# Function to check if Docker is running
check_docker() {
    if ! docker info > /dev/null 2>&1; then
        print_error "Docker is not running. Please start Docker and try again."
        exit 1
    fi
}

# Function to check if NVIDIA Docker is available
check_nvidia_docker() {
    if ! docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi > /dev/null 2>&1; then
        print_warning "NVIDIA Docker runtime not available. GPU acceleration may not work."
        print_warning "Install nvidia-docker2 or nvidia-container-toolkit for GPU support."
    else
        print_success "NVIDIA Docker runtime is available"
    fi
}

# Function to build Docker images
build_images() {
    print_status "Building Docker images..."
    
    # Build WhisperLive server image
    print_status "Building WhisperLive server image..."
    cd ../
    docker build -f docker/Dockerfile.gpu -t whisperlive-server .
    cd whisperlive-opt/
    
    # Build optimization harness image
    print_status "Building optimization harness image..."
    docker build -t whisperlive-opt .
    
    print_success "Docker images built successfully"
}

# Function to start WhisperLive server
start_server() {
    print_status "Starting WhisperLive server..."
    
    # Check if server is already running
    if docker-compose ps whisperlive-server | grep -q "Up"; then
        print_warning "WhisperLive server is already running"
        return 0
    fi
    
    # Start the server
    docker-compose up -d whisperlive-server
    
    # Wait for server to be ready
    print_status "Waiting for WhisperLive server to be ready..."
    timeout=60
    while [ $timeout -gt 0 ]; do
        if curl -s http://localhost:9090/health > /dev/null 2>&1; then
            print_success "WhisperLive server is ready"
            return 0
        fi
        sleep 2
        timeout=$((timeout - 2))
    done
    
    print_error "WhisperLive server failed to start within 60 seconds"
    docker-compose logs whisperlive-server
    exit 1
}

# Function to run optimization test
run_test() {
    print_status "Running optimization test..."
    print_status "Config: $CONFIG"
    print_status "Output: $OUTPUT"
    
    # Create output directory
    mkdir -p "$OUTPUT"
    
    # Run the test
    docker-compose --profile optimizer run --rm whisperlive-optimizer \
        python -m harness.runner \
        --config "$CONFIG" \
        --out "/app/results"
    
    # Copy results to host
    docker cp "$(docker-compose --profile optimizer ps -q whisperlive-optimizer):/app/results/." "$OUTPUT/"
    
    print_success "Test completed. Results saved to $OUTPUT"
    
    # Show summary
    if [ -f "$OUTPUT/summary.md" ]; then
        echo ""
        print_status "Test Summary:"
        echo "=============="
        head -20 "$OUTPUT/summary.md"
        echo ""
        print_status "Full results available in: $OUTPUT"
    fi
}

# Function to cleanup
cleanup() {
    print_status "Cleaning up Docker containers and volumes..."
    docker-compose down -v
    docker system prune -f
    print_success "Cleanup completed"
}

# Function to show status
show_status() {
    print_status "Docker Status:"
    echo "=============="
    docker-compose ps
    echo ""
    print_status "Container Logs (last 10 lines):"
    echo "====================================="
    docker-compose logs --tail=10 whisperlive-server
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -c|--config)
            CONFIG="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT="$2"
            shift 2
            ;;
        -b|--build)
            BUILD_IMAGES=true
            shift
            ;;
        -s|--start-server)
            START_SERVER=true
            shift
            ;;
        -t|--test)
            RUN_TEST=true
            shift
            ;;
        -a|--all)
            BUILD_IMAGES=true
            START_SERVER=true
            RUN_TEST=true
            shift
            ;;
        --cleanup)
            CLEANUP=true
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Main execution
main() {
    print_status "Starting Docker-based WhisperLive optimization testing..."
    
    # Check prerequisites
    check_docker
    check_nvidia_docker
    
    # Execute requested actions
    if [ "$CLEANUP" = true ]; then
        cleanup
        exit 0
    fi
    
    if [ "$BUILD_IMAGES" = true ]; then
        build_images
    fi
    
    if [ "$START_SERVER" = true ]; then
        start_server
    fi
    
    if [ "$RUN_TEST" = true ]; then
        run_test
    fi
    
    # Show final status
    show_status
    
    print_success "Docker-based optimization testing completed!"
}

# Run main function
main "$@"
