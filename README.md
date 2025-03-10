# Vexa – Secure Real-Time Meeting Transcription and Knowledge Management for Corporate In-House Deployment

<p align="left">
  <img src="assets/logodark.svg" alt="Vexa Logo" width="40"/>
</p>

Vexa is an enterprise-grade AI solution designed specifically for secure corporate environments where data security and compliance are non-negotiable. It enables professionals and teams to capture, transcribe, and transform meeting insights across platforms like **Google Meet**, **Microsoft Teams**, **Zoom**, **Discord**, **Slack**, and more into actionable knowledge.

Built as a multiuser, scalable microservice-based application, Vexa can efficiently service thousands of simultaneous users, converting speech to text in real-time and centralizing information for seamless knowledge extraction and controlled access.

## 📑 Table of Contents

1. [🚀 Release Status](#release-status)
2. [🌟 Features](#features)
3. [🏗 System Architecture](#system-architecture)
4. [📦 Repository Structure](#repository-structure)
5. [🛠 Technology Stack](#technology-stack)
6. [🚀 Local Setup Instructions](#local-setup-instructions)
7. [🔗 Quick Links](#quick-links)

---

## 🚀 Release Status

### Currently Available

- **[Real-Time Audio Transcription Service](https://github.com/Vexa-ai/vexa-transcription-service)**:
  - Real-time speech-to-text conversion
  - Secure on-premise deployment
  - Speaker detection
  - Low-latency performance (5-10 seconds)

### Upcoming Releases (March 2025)

- **Knowledge Management Module**:
  - Converts transcripts into structured insights
  - Contextual AI-powered search

- **Google Chrome Extension**:
  - Real-time transcription with AI assistance

👉 [Try Vexa for free](https://vexa.ai) – currently available as a SaaS for free testing at [vexa.ai](https://vexa.ai), allowing users to experience Vexa's capabilities directly in a managed environment.

---

## 🌟 Features

### During Meetings:

- Real-time transcription with speaker identification
- AI-driven contextual support and interactive chat

<p align="center">
  <img src="assets/extension.png" alt="Vexa Extension in Action" width="600"/>
  <br>
  <em>Chrome Extension: Real-time transcription and AI assistance during meetings</em>
</p>

### After Meetings:

- Intelligent knowledge extraction from conversations and documents
- Context-aware chat powered by advanced retrieval augmented generation (RAG)
- Enterprise-level data security with granular access controls

<p align="center">
  <img src="assets/dashboard.png" alt="Vexa Dashboard" width="600"/>
  <br>
  <em>Dashboard: Knowledge exploration and team collaboration</em>
</p>

---

## 🏗 System Architecture

Vexa employs a modular architecture ideal for enterprise environments requiring flexibility, scalability, and stringent security:

### User Interfaces

- **Google Chrome Extension**:
  - Enhanced real-time transcription
  - Interactive contextual assistance

- **Meeting and Knowledge Dashboard**:
  - Centralized knowledge repository
  - Advanced search and data exploration

### Backend Services

1. **Streamqueue Service**:
   - Captures and manages real-time audio streams

2. **Audio Service**:
   - Whisper-based, GPU-accelerated transcription

3. **Engine Service**:
   - Processes knowledge extraction and access logic

---

## 📦 Repository Structure

### Open Source Components

- **[Real-Time Audio Transcription Service](https://github.com/Vexa-ai/vexa-transcription-service)**:
  - Whisper integration for high-performance transcription
  - GPU acceleration with Ray Serve
  - Redis-backed fast data retrieval
  - Webhook integrations for flexible data flows

---

## 🛠 Technology Stack

- **Frontend**: React, Chrome Extension APIs
- **Backend**: Python 3.12+
- **Databases**: Redis, PostgreSQL, Qdrant, Elasticsearch
- **Infrastructure**: Docker, Docker Compose
- **AI Models**: Whisper, Openrouter for large language models

---

## 🚀 Local Setup Instructions

### Prerequisites

- Git
- Docker and Docker Compose
- NVIDIA GPU with CUDA
- Minimum 4GB RAM
- Stable internet connection

### Step 1: Clone Repository

```bash
git clone https://github.com/Vexa-ai/vexa
cd vexa
git submodule update --init --recursive
```

### Step 2: Set Up Whisper Service

```bash
cd whisper_service
cp .env.example .env
chmod +x start.sh
docker compose up -d
```

Check logs:

```bash
docker compose logs -f
```

### Step 3: Set Up Transcription Service

```bash
cd ../vexa-transcription-service
cp .env.example .env
# Set WHISPER_SERVICE_URL and WHISPER_API_TOKEN
docker compose up -d
```

### Step 4: Set Up Engine Service

```bash
cd ../vexa-engine
cp .env.example .env
docker compose up -d
# Optional clear existing transcripts
docker compose exec vexa-engine python clear_transcripts.py
```

### Step 5: Test System

```bash
cd ../vexa-testing-app
python register_test_user.py
python main.py
```

### Step 6: View Results

```bash
cd ../vexa-engine
docker compose exec vexa-engine python demo.py
```

### Troubleshooting

- Logs: `docker compose logs -f`
- Verify `.env` configurations
- Ensure GPU passthrough is correctly configured

---

## 🔗 Quick Links

- 🌐 [Vexa Website](https://vexa.ai)
- 💼 [LinkedIn](https://www.linkedin.com/company/vexa-ai/)
- 🐦 [X (@grankin_d)](https://x.com/grankin_d)
- 💬 [Discord Community](https://discord.gg/X8fU4Q2x)

⭐ Star this repository to stay updated on new releases!