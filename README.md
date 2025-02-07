# Vexa - AI Meeting Notetaker and Knowledge Chat for Professionals and Teams

NOTE: The service is live and running, currently being prepared for open sourcing. Planned public release - Late February 2025.

👉 Try it for free at [vexa.ai](https://vexa.ai)

![Vexa Logo](assets/logodark.svg)

Vexa is a powerful, distributed system for real-time meeting transcription, knowledge extraction, and analysis. It's designed to handle live audio streams from meetings and conversations, providing instant transcription and insights while delivering the full body of collected knowledge where it's needed most - all with fine-grained security and access control.

## 🚀 Features

During meetings:
- Real-time speaker-aware meeting transcription with AI contextual support and chat

After meetings:
- Meeting knowledge extraction from conversations and documents 
- RAG-powered chat with full company context awareness
- Fine-grained data access control

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

## 🛠 Technology Stack

- **Frontend**: React, Chrome Extension APIs
- **Backend**: Python 3.12+
- **Data Layer**: Redis, PostgreSQL, Qdrant, Elasticsearch
- **Infrastructure**: Docker, Docker Compose

## 🔗 Quick Links

- Product: [vexa.ai](https://vexa.ai)

---

⭐ Star this repository to get notified when it becomes public!