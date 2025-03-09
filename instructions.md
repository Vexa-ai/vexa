# Vexa - AI Meeting Notetaker and Knowledge Management System

This guide will help you set up the complete Vexa service stack locally. The system consists of several interconnected services which work together to provide AI-powered transcription and knowledge extraction capabilities.

## Prerequisites

- Git
- Docker and Docker Compose
- NVIDIA GPU with CUDA support (for optimal performance)
- At least 4GB of RAM
- Internet connection (for pulling Docker images)

## 1. Clone Repository and Initialize Submodules

First, clone the main Vexa repository and initialize all submodules:

```bash
git clone https://github.com/Vexa-ai/vexa
cd vexa
git submodule update --init --recursive
```

## 2. Set Up Whisper Service

The Whisper service handles speech-to-text conversion using OpenAI's Whisper model.

```bash
# Navigate to the whisper service directory
cd whisper_service

# Create environment configuration file
cp .env.example .env

# Modify the .env file with your desired settings
# (default values should work for most setups)

# Make the startup script executable
chmod +x start.sh

# Pull the pre-built Docker image
docker pull vexaai/ray-whisper:latest

# Start the service
docker compose up -d
```

### Verify Whisper Service is Running

You can check the service status by viewing the logs:

```bash
docker compose logs -f
```

The service is ready when you see "Service successfully deployed and running!" in the logs.

## 3. Set Up Transcription Service

The transcription service manages audio streams, handles transcription, performs speaker mapping, and saves transcripts to the engine service.

```bash
# Navigate to the transcription service directory
cd ../transcription-service

# Create environment configuration file
cp .env.example .env

# Update the .env file with:
# - WHISPER_SERVICE_URL: URL of your Whisper service
# - WHISPER_API_TOKEN: Token matching the one set in whisper_service/.env

# Start the service
docker compose up -d
```

## 4. Set Up Engine Service

The engine service handles the core business logic, knowledge extraction, and user interactions. In its slim version, it simply stores transcripts and makes them available through an API.

```bash
# Navigate to the engine service directory
cd ../engine

# Create environment configuration file (if not already present)
cp .env.example .env

# Start the service
docker compose up -d
```

## 5. Test the System

The vexa-testing-app can be used to test the complete system with mock data.

```bash
# Navigate to the testing app directory
cd ../testing-app

# Register a test user
python register_test_user.py

# Clear any existing transcripts (optional)
python clear_transcripts.py

# Start sending test data to the API
python main.py
```

## 6. View Transcription Results

To see the transcription results, open a new terminal and run:

```bash
# Open a shell in the backend service
docker compose exec backend bash

# Run the demo script
python demo.py
```

This will display the transcribed files from the system.

## Troubleshooting

- If any service fails to start, check the logs using `docker compose logs -f`
- Ensure all environment variables are correctly set in the respective .env files
- Verify that the services can communicate with each other (check network settings)
- For GPU-related issues, ensure your NVIDIA drivers and Docker configuration support GPU passthrough

## Additional Resources

For more detailed information about each component, refer to the README files in the respective directories:

- [Whisper Service](whisper_service/README.md)
- [Transcription Service](vexa-transcription-service/README.md)
- [Engine Service](vexa-engine/README.md)
- [Testing App](vexa-testing-app/README.md)








