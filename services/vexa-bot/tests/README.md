# Vexa Bot Tests

This directory contains various test scripts for the vexa-bot service.

## Test Scripts

### 1. `comprehensive-test.sh`
- **Purpose**: Comprehensive automated testing with 3 iterations
- **What it does**: Runs multiple bot instances sequentially to test overall functionality
- **Use case**: General stability testing and validation
- **Network**: Uses `vexa_dev_vexa_default` network
- **Validation**: Automated based on log analysis

### 2. `admission-debug-test.sh`
- **Purpose**: Debug bot's admission detection logic
- **What it does**: Single bot test with detailed log analysis and user validation
- **Use case**: Debugging when bot incorrectly thinks it's admitted vs waiting
- **Network**: Uses `vexa_dev_vexa_default` network
- **Validation**: Interactive user validation + automated log analysis

## Running Tests

### Prerequisites
1. Ensure Redis and other services are running:
   ```bash
   cd /home/dima/dev/vexa
   docker-compose up -d
   ```

2. Make sure you have access to the test meeting:
   - Meeting URL: `https://meet.google.com/uvn-edao-vyo`
   - Meeting ID: `wnr-jktr-drt`

### Running Comprehensive Test
```bash
cd /home/dima/dev/vexa/services/vexa-bot/tests
chmod +x comprehensive-test.sh
./comprehensive-test.sh
```

### Running Admission Debug Test
```bash
cd /home/dima/dev/vexa/services/vexa-bot/tests
chmod +x admission-debug-test.sh
./admission-debug-test.sh
```

## Test Configuration

Both tests use the same basic configuration:
- **Container Name**: Unique per test run
- **Image**: `vexa-bot:test` (built from `core/Dockerfile`)
- **Network**: `vexa_dev_vexa_default` (connects to Redis)
- **Meeting**: Google Meet test session
- **Capabilities**: `SYS_ADMIN` for browser automation
- **Memory**: 2GB shared memory for browser

## Debugging Admission Issues

The admission debug test helps identify when the bot:
1. **Incorrectly reports admission** when it's actually waiting
2. **Fails to detect waiting room** status
3. **Has People button detection issues**
4. **Missing transcription/audio activity**

### User Validation Process
During the admission debug test, you'll be asked to:
1. Check the actual Google Meet session
2. Verify if the bot is truly admitted or waiting
3. Assess the bot's video/audio presence
4. Provide your assessment for comparison with bot's self-assessment

## Network Troubleshooting

If you see Redis connection errors (`getaddrinfo EAI_AGAIN redis`):
1. Check that Redis is running: `docker-compose ps`
2. Verify network name: `docker network ls | grep vexa`
3. Ensure bot uses correct network: `vexa_dev_vexa_default`

## Adding New Tests

When adding new test scripts:
1. Follow the naming convention: `{purpose}-test.sh`
2. Include proper cleanup with `trap cleanup_container EXIT`
3. Use consistent color coding for output
4. Document the test purpose in this README
5. Test with the working network configuration
