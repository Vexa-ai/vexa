# Vexa - AI Meeting Notetaker and Knowledge Chat for Professionals and Teams

Extract  business knowledge from 
**Google Meet | Microsoft Teams | Zoom | Discord | Slack |** and more

## 🚀 Release Status

**Transcription Service**: ✅ Already published as an open-source component! Our enterprise-grade, real-time audio transcription system is [available now on GitHub](https://github.com/Vexa-ai/vexa-transcription-service). This production-ready service offers secure, private speech-to-text conversion with self-hosted and on-premise deployment options for maximum data sovereignty and compliance. Features include:
- Real-time transcription with advanced speaker detection
- Multi-platform support including Google Meet integration
- 5-10 second latency for live captions
- On-premise deployment for HIPAA, GDPR, and high-security environments
- Enterprise scalability supporting thousands of concurrent users

**Knowledge Management Module**: 🔜 Coming in March 2025. This component will transform meeting transcripts into structured knowledge and enable AI-powered contextual search and insights.


👉 Try it for free at [vexa.ai](https://vexa.ai)

<p align="left">
  <img src="assets/logodark.svg" alt="Vexa Logo" width="40"/>
</p>

Vexa combines the best of AI meeting notetakers (like **Otter.ai**, **Fathom.fm**, **Fireflies.ai**, **Tactiq.io**) with **powerful knowledge management** and **AI chat capabilities**. While other notetakers focus primarily on transcription and follow-ups, Vexa transforms your meetings into an **intelligent knowledge base** - imagine having **Claude Projects**' capabilities but trained specifically on your company's meeting knowledge, with **enterprise-grade security** and **access controls**.

Our distributed system provides:
- **Real-time transcription** with speaker detection
- **Instant knowledge extraction** from conversations
- **Contextual AI chat** that understands your company's entire meeting history
- **Enterprise security** with granular access management

## 🚀 Features

### During meetings:
Real-time speaker-aware meeting transcription with AI contextual support and chat

<p align="center">
  <img src="assets/extension.png" alt="Vexa Extension in Action" width="600"/>
  <br>
  <em>Chrome Extension: Real-time transcription and AI assistance during meetings</em>
</p>

### After meetings:
- Meeting knowledge extraction from conversations and documents 
- RAG-powered chat with full company context awareness
- Fine-grained data access control

<p align="center">
  <img src="assets/dashboard.png" alt="Vexa Dashboard" width="600"/>
  <br>
  <em>Dashboard: Knowledge exploration and team collaboration</em>
</p>

## 🏗 System Architecture

Built on scalable microservices architecture:

![System Architecture](assets/architecture-placeholder.png)

### User Interfaces

#### Google Chrome Extension
- Real-time transcripts with enhanced readability
- Interactive contextual support with single-click actions
- Contextual chat with access to company-wide knowledge base

#### Meeting and Chat Interface
- Company-wide data exploration by:
  - Speaker
  - Project
  - Meeting
- Advanced entity search:
  - People
  - Companies
  - Products
  - More

### Backend Architecture

#### 1. Streamqueue Service
- Real-time audio stream collection from client browser

#### 2. Audio Service
- Speech-to-text conversion using Whisper
- Load balancer for distributed model instances (coming soon)
- Speaker-aware transcription processing

#### 3. Engine Service
- Core business logic
- Knowledge extraction and accessibility

## 📦 Repository Structure

### Published Components

#### [Real-Time Audio Transcription Service](https://github.com/Vexa-ai/vexa-transcription-service)

Our **Real-Time Audio Transcription Service** is now publicly available as an open-source component on GitHub. This production-ready system offers enterprise-grade speech-to-text conversion with advanced speaker detection, designed for privacy and compliance.

Features:
- Real-time transcription with 5-10 second latency
- GPU acceleration with Whisper v3
- Redis-backed storage for fast retrieval
- Webhook-based integrations
- Enterprise-scale architecture supporting thousands of concurrent users
- Air-gapped deployment options for high-security environments

The full repository is available at: [github.com/Vexa-ai/vexa-transcription-service](https://github.com/Vexa-ai/vexa-transcription-service)


## 🛠 Technology Stack

- **Frontend**: React, Chrome Extension APIs
- **Backend**: Python 3.12+
- **Data Layer**: Redis, PostgreSQL, Qdrant, Elasticsearch
- **Infrastructure**: Docker, Docker Compose
- **AI**: local Whisper, Openrouter for LLM calls

## 🔗 Quick Links

- Product: [vexa.ai](https://vexa.ai)
- LinkedIn: [@vexa.ai](https://www.linkedin.com/company/vexa-ai/)
- X: [@grankin_d](https://x.com/grankin_d)
- Discord: [vexa discord server invite link](https://discord.gg/X8fU4Q2x)

---

⭐ Star this repository to get notified when it becomes public!